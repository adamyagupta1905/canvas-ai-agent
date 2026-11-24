"""The five per-course views on synthetic data (no LLM, no network)."""

import json
from datetime import date, datetime, timezone

import pytest

from canvas_agent import course_view as cv
from canvas_agent.tools import Store
from canvas_agent.user_inputs import InputStore, UserEntry

NOW = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def utc_course_time(monkeypatch):
    """Course-local time is the machine's zone in real use; pin it so tests give the same answer everywhere."""
    monkeypatch.setattr(cv, "local_tz", lambda: timezone.utc)

COURSE = {"name": "Synthetic SE", "weighted": True, "omit_from_final": [], "groups": [
    {"name": "Labs", "weight": 40, "rules": {}, "items": [
        ["Lab 1", "2026-09-10T06:59:00Z", 10, "graded", 9, False],
        ["Lab 2", "2026-11-27T06:59:00Z", 10, "unsubmitted", None, False]]},
    {"name": "Final exam", "weight": 60, "rules": {}, "items": []}],
    "modules": [["Unit 1: Coupling", ["P:Class 01: Coupling", "F:coupling.pdf", "A:Lab 1"]],
                ["Admin", ["A:Lab 2"]]]}

SITE_SCHEDULE = {"url": "https://x/cs101/schedule", "kind": "schedule", "title": "Schedule", "path": "pages/s.md",
                 "sha256": "x", "bytes": 1, "fetched_at": "2026-11-24T00:00:00Z", "links_out": [],
                 "tables": [[["Due", "Date", "What"], ["D4", "Fri 27 Nov, 18:00", "Build it"], ["Final", "Fri 18 Dec, 12:00", "cumulative"]],
                            [["Wk", "Dates", "Lectures"], ["1", "Sep 8, 10", "Intro"], ["13", "Dec 1, 3", "Review"]]]}
SLIDES = [
    {"id": "1", "url": "https://x/cs101/lectures/02a-coupling.pdf", "kind": "lecture", "title": "02a-coupling.pdf", "page": 1,
     "text": "02a - Cost of Change: Coupling\nCS101\nInstructor Name"},
    {"id": "2", "url": "https://x/cs101/lectures/02a-coupling.pdf", "kind": "lecture", "title": "02a-coupling.pdf", "page": 2,
     "text": "CS101 Software\nLearning Goals\nBy the end of the lecture you should be able to:\n●Understand the two forms of dependencies that can\nbe present between code."},
    {"id": "3", "url": "https://x/cs101/lectures/02a-coupling.pdf", "kind": "lecture", "title": "02a-coupling.pdf", "page": 3,
     "text": "CS101 Software\nDependencies in Code\nDegree, locality, strength"},
    {"id": "4", "url": "https://x/cs101/lectures/02a-coupling.pdf", "kind": "lecture", "title": "02a-coupling.pdf", "page": 4,
     "text": "CS101 Software\nconst x = () => {\nmore"},
    {"id": "5", "url": "https://x/cs101/lectures/01-course_intro.pdf", "kind": "lecture", "title": "01-course_intro.pdf", "page": 1,
     "text": "Today's plan\nwelcome"},
]


def make_store(tmp_path, with_site=True):
    (tmp_path / "canvas_snapshot.json").write_text(json.dumps({"courses": {"CS101": COURSE}}))
    facts = {"component_checks": [{"component": "Labs", "syllabus_weight": 40, "avg_syllabus_rules": 60.0},
                                  {"component": "Final exam", "syllabus_weight": 60, "avg_syllabus_rules": None}],
             "facts": {"exams": [{"kind": "final", "title": "Final exam", "exam_date": None}]},
             "exam_date_hints": []}
    (tmp_path / "syllabi").mkdir()
    (tmp_path / "syllabi" / "CS101.facts.json").write_text(json.dumps(facts))
    if with_site:
        site = tmp_path / "sites" / "CS101"
        site.mkdir(parents=True)
        (site / "manifest.json").write_text(json.dumps({"code": "CS101", "start_urls": ["https://x/cs101/"], "scopes": [],
                                                         "crawled_at": "2026-11-24", "docs": [SITE_SCHEDULE], "skipped": [],
                                                         "external_links": []}))
        (site / "index.json").write_text(json.dumps(SLIDES))
    return Store(tmp_path)


def test_parse_day_handles_both_orders_and_academic_year():
    today = date(2026, 11, 24)
    assert cv.parse_day("Sep 8, 10", today) == date(2026, 9, 8)
    assert cv.parse_day("Fri 27 Nov, 18:00", today) == date(2026, 11, 27)
    assert cv.parse_day("Mon 11 Jan", today) == date(2027, 1, 11)
    assert cv.parse_day("no date here", today) is None


def test_progress_prefers_schedule_weeks(tmp_path):
    p = cv.progress(make_store(tmp_path), "CS101", NOW)
    assert p["weeks"] == {"passed": 1, "total": 2, "source": "https://x/cs101/schedule"}
    assert p["course_progress_pct"] == 50.0
    assert p["grade"]["current_pct"] == 90.0 and p["grade"]["basis"] == "Canvas setup"


def test_notes_from_slides_clean_titles_goals_and_boilerplate(tmp_path):
    n = cv.notes(make_store(tmp_path), "CS101", NOW)
    assert [s["title"] for s in n] == ["01 · Course intro", "02a - Cost of Change: Coupling"]  # file-name title when page 1 has none
    deck = n[1]
    assert deck["goals"] == ["Understand the two forms of dependencies that can be present between code."]  # wrapped line joined
    assert deck["points"] == ["Dependencies in Code"]  # repeated header removed, code line dropped
    assert deck["source"].endswith("02a-coupling.pdf")


def test_notes_fall_back_to_canvas_modules_with_scores(tmp_path):
    n = cv.notes(make_store(tmp_path, with_site=False), "CS101", NOW)
    assert n[0]["title"] == "Unit 1: Coupling" and n[0]["points"] == ["Class 01: Coupling", "coupling"]
    assert n[0]["scores"] == [{"item": "Lab 1", "pct": 90.0}]
    assert len(n) == 1  # module with no content items is skipped


def test_deadlines_merge_sources_and_mark_exams(tmp_path):
    d = cv.deadlines(make_store(tmp_path), "CS101", NOW)
    whats = [x["what"] for x in d]
    assert "Lab 2" in whats and "D4 Build it" in whats
    final = next(x for x in d if x["what"].startswith("Final"))
    assert final["status"] == "exam" and final["pct_of_grade"] == 60
    assert all(x["when"].tzinfo is not None for x in d)
    assert d == sorted(d, key=lambda x: x["when"])


def test_plan_rules_work_blocks_and_exam_review_with_weak_areas_first(tmp_path):
    store = make_store(tmp_path)
    InputStore(tmp_path / "user_inputs.json").add(UserEntry(course="CS101", component="Labs", item="Lab 1",
                                                            improvement_areas=["recursion"]))
    p = cv.plan(store, "CS101", NOW)
    tasks = [(d["date"], t["task"]) for d in p["days"] for t in d["tasks"]]
    assert ("2026-11-26", "Work on: Lab 2") in tasks  # day before it is due (local time)
    reviews = [t for _, t in tasks if t.startswith("Review for Final")]
    assert len(reviews) == 4 and reviews[0].endswith("recursion")  # student's own area first
    assert any(t.endswith("Labs (average 60%)") for t in reviews)  # then low components
    assert tasks[-1][1].startswith("Exam: Final") and p["to"] == "2026-12-18"
    assert p["weak_areas"][:2] == ["recursion", "Labs (average 60%)"]


def test_overview_has_one_row_per_course(tmp_path):
    rows = cv.overview(make_store(tmp_path), NOW)
    assert rows[0]["code"] == "CS101" and rows[0]["grade_pct"] == 90.0 and rows[0]["next_deadline"]


CONTRACT_COURSE_KEYS = {"code", "name", "grade", "progress", "needed", "pass_requirements", "warnings", "input_requests",
                        "components", "notes", "plan", "deadlines", "sources"}


def test_dashboard_export_matches_the_ui_contract(tmp_path):
    from scripts.export_dashboard import export

    out = export(NOW, make_store(tmp_path).root)
    assert set(out) == {"generated_at", "courses", "ask_examples"}
    c = out["courses"][0]
    assert set(c) == CONTRACT_COURSE_KEYS
    assert set(c["grade"]) >= {"current_pct", "weight_graded_pct", "floor_pct", "ceiling_pct", "basis"}
    assert set(c["progress"]) == {"course_progress_pct", "label"} and c["progress"]["label"] == "Week 1 of 2"
    assert set(c["plan"]) == {"weak_areas", "days"}
    assert all(set(d) == {"when", "what", "pct_of_grade", "status", "sources", "days_left"} for d in c["deadlines"])
    assert all(set(n) == {"order", "label", "title", "goals", "points", "scores", "source_url"} for n in c["notes"])
