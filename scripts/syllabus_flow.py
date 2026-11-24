"""Run the standard syllabus flow on PDFs in data/syllabi/ (gitignored).

    # free, offline: text/OCR, chunking and retrieval only; shows what WOULD be sent to the model
    .venv/bin/python -m scripts.syllabus_flow LAW201 IS301 BUS201

    # paid: also runs LLM extraction (LLM_PROVIDER from .env, default gemini) + checks + Canvas reconcile
    .venv/bin/python -m scripts.syllabus_flow LAW201 --live

Expects data/syllabi/<COURSE>.pdf. Writes data/syllabi/<COURSE>.retrieval.json and, with --live,
data/syllabi/<COURSE>.facts.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from canvas_agent.documents import extract_pages
from canvas_agent.retrieval import chunk_pages
from canvas_agent.syllabus_flow import retrieve, run_syllabus_flow

ROOT = Path(__file__).resolve().parent.parent
SYL = ROOT / "data" / "syllabi"
DATA = ROOT / "data"
SNAP = DATA / "canvas_snapshot.json"


def syllabus_source(code: str) -> tuple[str, list]:
    """The syllabus PDF in data/syllabi/ if present, else the syllabus page of the crawled course website."""
    pdf = SYL / f"{code}.pdf"
    if pdf.exists():
        return str(pdf), extract_pages(pdf)
    from canvas_agent.website import doc_pages, find_doc

    site = DATA / "sites" / code
    doc = find_doc(site, "syllabus")
    if doc:
        return doc.url, doc_pages(site, doc)
    raise SystemExit(f"No syllabus for {code}: add data/syllabi/{code}.pdf or crawl its website (scripts.crawl_site)")


def dry_run(code: str, pdf: Path) -> None:
    _, pages = syllabus_source(code)
    chunks = chunk_pages(code, pages)
    by_topic = retrieve(chunks)
    out = {
        "course": code, "pages": len(pages),
        "methods": {p.page: p.method for p in pages},
        "chunks": len(chunks),
        "retrieved": {t: [{"id": c.id, "page": c.page, "preview": c.text[:160]} for c in cs] for t, cs in by_topic.items()},
    }
    (SYL / f"{code}.retrieval.json").write_text(json.dumps(out, indent=1))
    ocr = [p for p, m in out["methods"].items() if m == "ocr"]
    print(f"{code}: {len(pages)} pages ({len(ocr)} OCR: {ocr}), {len(chunks)} chunks; "
          + ", ".join(f"{t}: {len(cs)} chunks" for t, cs in by_topic.items()))


def live_run(code: str, pdf: Path) -> None:
    from dotenv import load_dotenv

    from canvas_agent.llm import make_client

    load_dotenv(ROOT / ".env")  # keys are read by the SDKs; never printed
    source, pages = syllabus_source(code)
    post_process(code, run_syllabus_flow(source, code, make_client(), pages=pages))


def recheck(code: str, pdf: Path) -> None:
    """Free: replay the saved raw LLM output (kept + previously rejected items) through the current
    validation and Python checks. No model call."""
    from canvas_agent.syllabus_flow import (ComponentRules, SyllabusFacts, SyllabusReport, validate,
                                            validate_rules)

    saved = json.loads((SYL / f"{code}.facts.json").read_text())
    chunks = chunk_pages(code, syllabus_source(code)[1])
    rej = saved.get("rejected", [])
    raw_facts = SyllabusFacts.model_validate({
        sec: saved["facts"][sec] + [r["item"] for r in rej if r["section"] == sec]
        for sec in ("components", "exams", "policies")})
    facts, rejected, checks = validate(raw_facts, chunks)
    raw_rules = ComponentRules.model_validate({"rules": saved.get("rules", []) + [
        r["item"] for r in rej if r["section"] == "rules" and "rule kept" not in r["reason"]]})
    rules, rrej = validate_rules(raw_rules, chunks, facts.components)
    fields = SyllabusReport.model_fields
    base = {k: v for k, v in saved.items() if k in fields}
    post_process(code, SyllabusReport.model_validate(base | {
        "facts": facts.model_dump(mode="json"), "rules": [r.model_dump(mode="json") for r in rules],
        "rejected": [r.model_dump(mode="json") for r in rejected + rrej],
        "checks": [c.model_dump() for c in checks]}))


def post_process(code: str, report) -> None:
    from canvas_agent.reconcile import reconcile
    from canvas_agent.component_check import attendance_check, check_components, map_items_to_components, syllabus_view_course
    from canvas_agent.grades import grade_summary

    out = report.model_dump(mode="json")
    course = json.loads(SNAP.read_text())["courses"].get(code) if SNAP.exists() else None
    from canvas_agent.tools import Store

    store = Store(DATA)
    if course is not None:
        course = store.courses().get(code, course)  # PrairieLearn merged into Canvas gaps
        if not course["weighted"]:  # Canvas is only a points mirror: use PrairieLearn's own groupings if available
            from canvas_agent.prairielearn import pl_course

            plc = pl_course(store.pl(), code, course.get("name", ""), canvas=course)
            if plc:
                plc["modules"] = course.get("modules", [])
                course = plc
    if course:
        recon = reconcile(report.facts.components, course)
        ov_path = DATA / "item_overrides.json"  # {"BUS201": {"Canvas item name": "Syllabus component" | null}}
        overrides = json.loads(ov_path.read_text()).get(code, {}) if ov_path.exists() else {}
        checks = check_components(report.facts.components, report.rules, course, recon, overrides)
        out["reconcile"] = [r.model_dump() for r in recon]
        out["component_checks"] = [c.model_dump() for c in checks]
        quotes = [r.source_quote for r in report.rules] + [p.source_quote for p in report.facts.policies]
        # Attendance thresholds are read by Python straight from the syllabus text, sentence by sentence.
        import re as _re
        full = " ".join(pg.text for pg in syllabus_source(code)[1])
        quotes += [x.strip() for x in _re.split(r"(?<=[.!?])\s+", _re.sub(r"\s+", " ", full))
                   if _re.search(r"miss\w*\s+more than \d+(?:\.\d+)?\s*%", x, _re.I)]
        conf_path = DATA / "confirmations.json"  # things the student confirmed, e.g. absences
        confirmed = json.loads(conf_path.read_text()).get(code, {}) if conf_path.exists() else {}
        att = attendance_check(checks, quotes, course, confirmed.get("absences"))
        out["attendance_check"] = att.model_dump() if att else None
        from canvas_agent.component_check import exam_date_hints
        out["exam_date_hints"] = [h.model_dump() for h in exam_date_hints(
            out["facts"]["exams"], full, course, year=2026)]
        from datetime import date

        from canvas_agent.component_check import past_exams_without_score
        canvas_course = json.loads(SNAP.read_text())["courses"].get(code, {})
        grade_tools = [t for t in canvas_course.get("tools", []) if t.lower() in ("gradescope", "prairielearn")]
        out["past_exams_without_score"] = past_exams_without_score(
            out["facts"]["exams"], out["exam_date_hints"], checks, date.today().isoformat(), grade_tools)
        if not course["weighted"]:
            out["item_mapping"] = [m.model_dump() for m in map_items_to_components(report.facts.components, course, overrides)]
        if report.facts.components:
            from canvas_agent.grades import needed_on_remaining

            from canvas_agent.user_inputs import InputStore, apply_entries, input_requests

            entries = InputStore(DATA / "user_inputs.json").for_course(code)
            view = syllabus_view_course(report.facts.components, checks, course)
            view, out["user_entries_applied"] = apply_entries(view, entries)  # your Gradescope etc. grades
            entered = {e.component for e in entries if e.score is not None}
            out["past_exams_without_score"] = [p for p in out.get("past_exams_without_score", [])
                                               if not any(p.startswith(c) for c in entered)]
            out["input_requests"] = [r.model_dump() for r in input_requests(code, out, entries, grade_tools)]
            out["syllabus_view_course"] = view
            out["syllabus_view_grade"] = grade_summary(code, view).model_dump(mode="json")
            out["needed"] = {str(t): needed_on_remaining(view, t) for t in (60, 70, 80, 90)}
        out["canvas_view_grade"] = grade_summary(code, course).model_dump(mode="json")
    (SYL / f"{code}.facts.json").write_text(json.dumps(out, indent=1))
    print(f"{code}: {len(report.facts.components)} components, {len(report.rules)} rules, {len(report.facts.exams)} exams, "
          f"{len(report.facts.policies)} policies, {len(report.rejected)} rejected")
    for c in report.checks:
        print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("courses", nargs="+")
    ap.add_argument("--live", action="store_true", help="run the paid LLM extraction step")
    ap.add_argument("--recheck", action="store_true", help="free: rerun Python checks on the saved extraction")
    args = ap.parse_args()
    SYL.mkdir(parents=True, exist_ok=True)
    for c in args.courses:
        syllabus_source(c)  # exits with a clear message if neither a PDF nor a crawled site exists
    for code in args.courses:
        (live_run if args.live else recheck if args.recheck else dry_run)(code, SYL / f"{code}.pdf")


if __name__ == "__main__":
    main()
