"""Build demo_data/: two fictional courses run through the real pipeline, for the keyless demo.

    .venv/bin/python -m scripts.make_demo_data
    .venv/bin/streamlit run app.py        # with no data/ folder, the dashboard opens this demo

Everything here is invented (course names, people, scores). The syllabus "LLM" step uses recorded responses
(a scripted model), so the real grounding checks, Canvas reconciliation, grade engine, notes, plan and deadline
views all run without an API key. The demo's "today" is frozen in demo_data/demo_meta.json.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo_data"
AS_OF = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- Canvas snapshot (fictional)
def cs101() -> dict:
    lab_due = [datetime(2026, 9, 18, 6, 59, tzinfo=timezone.utc) + timedelta(weeks=i) for i in range(7)]
    lab_due.append(datetime(2026, 11, 27, 6, 59, tzinfo=timezone.utc))  # Lab 8: Thu Nov 26 23:59 Pacific
    lab_scores = [10, 9, 10, 8, 10, 0, 10, None]
    labs = [[f"Lab {i + 1}", iso(lab_due[i]), 10, "graded" if s is not None else "unsubmitted", s, False]
            for i, s in enumerate(lab_scores)]
    return {"canvas_id": 101, "name": "Software Design", "course_code": "CS101", "weighted": True,
            "omit_from_final": [], "canvas_totals": None, "modules": [],
            "groups": [
                {"name": "Labs", "weight": 10, "rules": {"drop_lowest": 1}, "items": labs},
                {"name": "Project", "weight": 30, "rules": {}, "items": [
                    ["D1 Requirements", "2026-09-26T01:00:00Z", 100, "graded", 70, False],
                    ["D2 Design", "2026-10-17T01:00:00Z", 100, "graded", 90, False],
                    ["D3 Implementation", "2026-11-07T02:00:00Z", 100, "pending_review", None, False],
                    ["D4 Final release", "2026-11-28T02:00:00Z", 100, "unsubmitted", None, False]]},
                {"name": "Midterm", "weight": 20, "rules": {}, "items": [["Midterm", None, 100, "graded", 78, False]]},
                {"name": "Final exam", "weight": 40, "rules": {}, "items": []}]}


LAW_TOPICS = ["What is law", "Sources of law", "The courts", "Offer and acceptance", "Consideration",
              "Capacity", "Defects", "Privity", "Interpretation", "Discharge", "Remedies", "Midterm review",
              "Exclusion clauses", "Sale of goods", "Negligence", "Misstatement", "Intellectual property",
              "Agency", "Fiduciary duty", "Partnerships", "Corporations"]


def law201() -> dict:
    start = datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc)
    scores = [4, 0, 5, 3, 0, 4.5, 4, 5, 3.5, 0, 4, 5, None, 4.5, 0, 4, 3.5, 4.5, None, None, None]
    items, modules, unit = [], [], []
    for n in range(1, 22):
        due = start + timedelta(days=3.5 * (n - 1))
        unit += [f"P:Class {n:02d}: {LAW_TOPICS[n - 1]}"]
        if n != 13:  # class 13 has no exercise, by design
            s = scores[n - 1]
            items.append([f"Class {n:02d} In-Class Exercise", iso(due), 5, "graded" if s is not None else "unsubmitted", s, False])
            unit += [f"A:Class {n:02d} In-Class Exercise"]
        if n % 7 == 0:
            modules.append([f"Unit {n // 7}", unit])
            unit = []
    return {"canvas_id": 201, "name": "Commercial Law", "course_code": "LAW201", "weighted": True,
            "omit_from_final": [], "canvas_totals": None, "modules": modules,
            "groups": [
                {"name": "In-Class Exercises", "weight": 10, "rules": {"drop_lowest": 2}, "items": items},
                {"name": "Midterm", "weight": 35, "rules": {}, "items": [["Midterm", None, 100, "graded", 84, False]]},
                {"name": "Final Exam", "weight": 40, "rules": {}, "items": []},
                {"name": "Oral Exam", "weight": 15, "rules": {}, "items": [["Oral exam", None, 15, "unsubmitted", None, False]]}]}


# ---------------------------------------------------------------- CS101 course website (fictional)
CS101_SYLLABUS = """# Syllabus

## Grading

Component | Weight
Labs | 10%
Project | 30%
Midterm | 20%
Final exam | 40%

## Labs

There are 8 labs. The lowest lab mark is dropped. Late labs are not graded.

## Project

The project has 4 deliverables, each weighted equally.

## Exams

The midterm is on October 22. The final exam is on December 15.

You must average at least 50% across the midterm and final exam to pass the course.
"""

CS101_DECKS = {
    "01-introduction.pdf": ["01 - Introduction to Software Design\nCS101\nDemo Instructor",
                            "CS101\nLearning Goals\n●Explain why design decisions change the cost of future work.",
                            "CS101\nWhat is software design\nTrade-offs everywhere", "CS101\nCourse roadmap\nLabs and project"],
    "02-coupling.pdf": ["02 - Coupling and Cohesion\nCS101\nDemo Instructor",
                        "CS101\nLearning Goals\n●Identify coupling and evaluate the cost of a change.",
                        "CS101\nDependencies in code\nDegree, locality, strength", "CS101\nCohesion\nKinds of binding"],
    "03-testing.pdf": ["03 - Testing and Test Doubles\nCS101\nDemo Instructor",
                       "CS101\nLearning Goals\n●Write tests that survive refactoring.",
                       "CS101\nUnit tests\nArrange, act, assert", "CS101\nTest doubles\nStubs, mocks and fakes"],
    "04-patterns.pdf": ["04 - Design Patterns\nCS101\nDemo Instructor",
                        "CS101\nLearning Goals\n●Choose a pattern that makes a likely change cheap.",
                        "CS101\nAdapter\nWrapping a legacy API", "CS101\nObserver\nDecoupling publishers from subscribers"],
    "05-apis.pdf": ["05 - API Design and Versioning\nCS101\nDemo Instructor",
                    "CS101\nLearning Goals\n●Evolve an API without breaking its clients.",
                    "CS101\nREST resources\nNouns, verbs and status codes", "CS101\nSemantic versioning\nMajor, minor, patch"],
}


def cs101_site(site: Path) -> None:
    from canvas_agent.website import Manifest, SiteDoc

    (site / "pages").mkdir(parents=True)
    (site / "pages" / "syllabus.md").write_text(f"<!-- source: https://course.example.org/cs101/syllabus -->\n{CS101_SYLLABUS}")
    (site / "pages" / "schedule.md").write_text("<!-- source: https://course.example.org/cs101/schedule -->\n# Schedule\n")
    weeks = [["Wk", "Dates", "Lectures"]] + [[str(w + 1), (datetime(2026, 9, 8) + timedelta(weeks=w)).strftime("%b %-d"),
                                              f"Week {w + 1}"] for w in range(13)]
    deadlines = [["Due", "Date", "What"], ["Lab 8", "Thu 26 Nov, 23:59", "Testing"],
                 ["D4", "Fri 27 Nov, 18:00", "Final release"], ["Final", "Tue 15 Dec, 12:00", "Cumulative"]]
    now = iso(AS_OF)
    docs = [SiteDoc(url="https://course.example.org/cs101/syllabus", kind="syllabus", title="Syllabus", path="pages/syllabus.md",
                    sha256="demo", bytes=len(CS101_SYLLABUS), fetched_at=now, headings=["Grading", "Labs", "Project", "Exams"]),
            SiteDoc(url="https://course.example.org/cs101/schedule", kind="schedule", title="Schedule", path="pages/schedule.md",
                    sha256="demo", bytes=1, fetched_at=now, tables=[deadlines, weeks])]
    index = []
    for name, pages in CS101_DECKS.items():
        url = f"https://course.example.org/cs101/lectures/{name}"
        docs.append(SiteDoc(url=url, kind="lecture", title=name, path=f"files/{name}", sha256="demo", bytes=1, fetched_at=now))
        index += [{"id": f"CS101-{name}-p{i + 1}", "url": url, "kind": "lecture", "title": name, "page": i + 1,
                   "text": text, "sha256": "demo"} for i, text in enumerate(pages)]
    (site / "index.json").write_text(json.dumps(index, indent=1))
    m = Manifest(code="CS101", start_urls=["https://course.example.org/cs101/"], scopes=["/cs101/"], crawled_at=now,
                 docs=docs, skipped=[], external_links=[])
    (site / "manifest.json").write_text(m.model_dump_json(indent=1))


LAW201_SYLLABUS = (
    "LAW 201 Commercial Law (fictional demo course)\n\n"
    "Grade breakdown: In-class exercises 10%, Midterm exam 35%, Final exam 40%, Oral exam 15%.\n\n"
    "There is one in-class exercise in each class. Your two lowest in-class exercise scores will be dropped. "
    "In-class exercises cannot be made up.\n\n"
    "Midterm Examination Date: October 29.\n\nThe final exam is on December 18.\n\n"
    "Students who miss more than 15% of classes will receive an attendance warning. "
    "Students missing more than 20% of classes may be excluded from the final exam.\n"
)


def law201_pdf(path: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, LAW201_SYLLABUS)
    pdf.output(str(path))


# ---------------------------------------------------------------- recorded "LLM" responses
def recorded(code: str):
    from canvas_agent.syllabus_flow import ComponentRule as R, ComponentRules, ExamFact, GradeComponent as C, PolicyFact, SyllabusFacts

    if code == "CS101":
        c = "CS101-p1-c1"
        facts = SyllabusFacts(
            components=[C(name="Labs", weight_pct=10, chunk_id=c, source_quote="Labs | 10%"),
                        C(name="Project", weight_pct=30, chunk_id=c, source_quote="Project | 30%"),
                        C(name="Midterm", weight_pct=20, chunk_id=c, source_quote="Midterm | 20%"),
                        C(name="Final exam", weight_pct=40, chunk_id=c, source_quote="Final exam | 40%")],
            exams=[ExamFact(kind="midterm", title="Midterm", exam_date="2026-10-22", when_text="October 22", chunk_id=c,
                            source_quote="The midterm is on October 22."),
                   ExamFact(kind="final", title="Final exam", exam_date="2026-12-15", when_text="December 15", chunk_id=c,
                            source_quote="The final exam is on December 15.")],
            policies=[PolicyFact(kind="drop_rule", summary="The lowest lab is dropped.", chunk_id=c,
                                 source_quote="The lowest lab mark is dropped.")])
        q = "You must average at least 50% across the midterm and final exam to pass the course."
        rules = ComponentRules(rules=[
            R(component="Labs", kind="item_count", value=8, summary="There are 8 labs.", chunk_id=c, source_quote="There are 8 labs."),
            R(component="Labs", kind="drop_lowest", value=1, summary="The lowest lab mark is dropped.", chunk_id=c,
              source_quote="The lowest lab mark is dropped."),
            R(component="Labs", kind="late_penalty", summary="Late labs are not graded.", chunk_id=c,
              source_quote="Late labs are not graded."),
            R(component="Project", kind="item_count", value=4, summary="Four deliverables.", chunk_id=c,
              source_quote="The project has 4 deliverables, each weighted equally."),
            R(component="Project", kind="aggregation", aggregation="equal_weight", summary="Deliverables weighted equally.",
              chunk_id=c, source_quote="The project has 4 deliverables, each weighted equally."),
            R(component="Midterm", kind="pass_requirement", value=50, summary="Exams must average 50%.", chunk_id=c, source_quote=q),
            R(component="Final exam", kind="pass_requirement", value=50, summary="Exams must average 50%.", chunk_id=c, source_quote=q)])
    else:
        c = "LAW201-p1-c1"
        facts = SyllabusFacts(
            components=[C(name="In-class exercises", weight_pct=10, chunk_id=c, source_quote="In-class exercises 10%"),
                        C(name="Midterm exam", weight_pct=35, chunk_id=c, source_quote="Midterm exam 35%"),
                        C(name="Final exam", weight_pct=40, chunk_id=c, source_quote="Final exam 40%"),
                        C(name="Oral exam", weight_pct=15, chunk_id=c, source_quote="Oral exam 15%")],
            exams=[ExamFact(kind="midterm", title="Midterm exam", exam_date="2026-10-29", when_text="October 29", chunk_id=c,
                            source_quote="Midterm Examination Date: October 29."),
                   ExamFact(kind="final", title="Final exam", exam_date="2026-12-18", when_text="December 18", chunk_id=c,
                            source_quote="The final exam is on December 18.")],
            policies=[PolicyFact(kind="pass_requirement", summary="Missing more than 20% of classes may exclude you from the final.",
                                 chunk_id=c, source_quote="Students missing more than 20% of classes may be excluded from the final exam.")])
        rules = ComponentRules(rules=[
            R(component="In-class exercises", kind="drop_lowest", value=2, summary="Two lowest dropped.", chunk_id=c,
              source_quote="Your two lowest in-class exercise scores will be dropped."),
            R(component="In-class exercises", kind="missed_work", summary="No make-ups.", chunk_id=c,
              source_quote="In-class exercises cannot be made up.")])
    return facts, rules


class RecordedLLM:
    def __init__(self, facts, rules):
        self.by_schema = {type(facts): facts, type(rules): rules}

    def structured(self, system, prompt, schema):
        return self.by_schema[schema]


def main() -> None:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    (DEMO / "syllabi").mkdir(parents=True)
    os.environ["COURSE_DATA_DIR"] = str(DEMO)  # before importing the pipeline modules
    (DEMO / "demo_meta.json").write_text(json.dumps({"as_of": AS_OF.isoformat(), "note": "Fictional demo data."}, indent=1))
    (DEMO / "canvas_snapshot.json").write_text(json.dumps(
        {"_meta": {"source": "fictional demo"}, "courses": {"CS101": cs101(), "LAW201": law201()}}, indent=1))
    cs101_site(DEMO / "sites" / "CS101")
    law201_pdf(DEMO / "syllabi" / "LAW201.pdf")

    from scripts import syllabus_flow as sf
    from canvas_agent.syllabus_flow import run_syllabus_flow

    for code in ("CS101", "LAW201"):
        source, pages = sf.syllabus_source(code)
        report = run_syllabus_flow(source, code, RecordedLLM(*recorded(code)), pages=pages)
        sf.post_process(code, report)
        facts = json.loads((DEMO / "syllabi" / f"{code}.facts.json").read_text())
        g = facts["syllabus_view_grade"]
        print(f"{code}: {len(report.facts.components)} components, {len(report.rules)} rules, {len(report.rejected)} rejected; "
              f"grade {g['current_pct']}% over {g['weight_determined']}% graded")


if __name__ == "__main__":
    main()
