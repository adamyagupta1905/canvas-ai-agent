"""One command from Canvas access (or a user-supplied file) to grades + syllabus facts.

    # Canvas token in .env (CANVAS_BASE_URL, CANVAS_TOKEN). Free: pulls data, finds + downloads syllabi,
    # runs text/OCR + retrieval. Nothing is written to Canvas.
    .venv/bin/python -m scripts.ingest_canvas --term 2026

    # specific courses, and the paid LLM extraction step
    .venv/bin/python -m scripts.ingest_canvas --course-ids 193001 191178 --live

    # user just hands over a file (no Canvas needed for this step)
    .venv/bin/python -m scripts.ingest_canvas --file ~/Downloads/syllabus.pdf --course LAW201 [--live]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from canvas_agent.ingest import SyllabusSource, course_code, fetch_syllabus, load_pages, snapshot_course, source_from_user_file
from canvas_agent.retrieval import chunk_pages
from canvas_agent.syllabus_flow import retrieve, run_syllabus_flow

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYL = DATA / "syllabi"
SNAP = DATA / "canvas_snapshot.json"


def _save_snapshot(code: str, course: dict) -> None:
    snap = json.loads(SNAP.read_text()) if SNAP.exists() else {"_meta": {}, "courses": {}}
    snap["courses"][code] = {k: v for k, v in course.items() if not k.startswith("_")}
    snap["_meta"]["source"] = "Canvas REST API, read-only token"
    SNAP.write_text(json.dumps(snap, indent=1))


def _run_flow(code: str, src: SyllabusSource, live: bool) -> None:
    print(f"  syllabus: {src.how}")
    pages = load_pages(src)
    if not pages:
        print("  no syllabus text available; skipping extraction")
        return
    if not live:
        chunks = chunk_pages(code, pages)
        by_topic = retrieve(chunks)
        ocr = [p.page for p in pages if p.method == "ocr"]
        print(f"  {len(pages)} pages ({len(ocr)} OCR), {len(chunks)} chunks; "
              + ", ".join(f"{t}: {len(c)}" for t, c in by_topic.items()) + "  (dry run: no LLM call)")
        return
    from canvas_agent.llm import make_client
    from canvas_agent.reconcile import reconcile

    report = run_syllabus_flow(src.path or "", code, make_client(), pages=pages)
    out = report.model_dump(mode="json") | {"source_trace": src.how}
    if SNAP.exists() and (course := json.loads(SNAP.read_text())["courses"].get(code)):
        out["reconcile"] = [r.model_dump() for r in reconcile(report.facts.components, course)]
    (SYL / f"{code}.facts.json").write_text(json.dumps(out, indent=1))
    for c in report.checks:
        print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course-ids", nargs="*", type=int)
    ap.add_argument("--term", help="only active courses whose term name contains this, e.g. 2026")
    ap.add_argument("--file", help="a syllabus file the user provides (PDF or text)")
    ap.add_argument("--course", help="course code to file --file under, e.g. LAW201")
    ap.add_argument("--live", action="store_true", help="run the paid LLM extraction step")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")  # values are read by the clients; never printed
    SYL.mkdir(parents=True, exist_ok=True)

    if args.file:
        if not args.course:
            ap.error("--file needs --course")
        print(f"{args.course}:")
        _run_flow(args.course, source_from_user_file(Path(args.file).expanduser()), args.live)
        return

    from canvas_agent.canvas_api import CanvasAPI

    api = CanvasAPI()
    try:
        courses = api.active_courses()
        if args.course_ids:
            courses = [c for c in courses if c["id"] in set(args.course_ids)]
        if args.term:
            courses = [c for c in courses if args.term in ((c.get("term") or {}).get("name") or "")]
        for c in courses:
            code = course_code(c)
            print(f"{code} ({c['id']}):")
            snap = snapshot_course(api, c["id"])
            _save_snapshot(code, snap)
            print(f"  snapshot: {sum(len(g['items']) for g in snap['groups'])} items in {len(snap['groups'])} groups")
            _run_flow(code, fetch_syllabus(api, snap, SYL), args.live)
    finally:
        api.close()


if __name__ == "__main__":
    main()
