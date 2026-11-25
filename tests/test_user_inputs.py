"""Student-provided grades and improvement areas (for Gradescope, iClicker, paper exams)."""

import json

import pytest

from canvas_agent.grades import grade_summary, needed_on_remaining
from canvas_agent.user_inputs import InputStore, UserEntry, apply_entries, input_requests, split_areas, study_focus

FACTS = {
    "past_exams_without_score": ["Midterm (25%) was scheduled on or around 2026-10-29 but has no score in Canvas or PrairieLearn yet."],
    "component_checks": [
        {"component": "Labs", "syllabus_weight": 35, "items_in_canvas": 2, "scored": 2},
        {"component": "Participation", "syllabus_weight": 5, "items_in_canvas": 0, "scored": 0},
        {"component": "Midterm", "syllabus_weight": 25, "items_in_canvas": 0, "scored": 0},
        {"component": "Final exam", "syllabus_weight": 35, "items_in_canvas": 0, "scored": 0},
    ],
}
VIEW = {"name": "Synthetic SE", "weighted": True, "groups": [
    {"name": "Labs", "weight": 35, "rules": {}, "items": [["Lab 1", None, 10, "graded", 10, False], ["Lab 2", None, 10, "graded", 8, False]]},
    {"name": "Participation", "weight": 5, "rules": {}, "items": []},
    {"name": "Midterm", "weight": 25, "rules": {}, "items": [["Midterm (not yet in Canvas) #1", None, 40, "unsubmitted", None, False]]},
    {"name": "Final exam", "weight": 35, "rules": {}, "items": []}]}


def test_split_areas():
    assert split_areas("LSP vs DIP; test doubles\n- REST status codes, mocking (stubs, fakes)") == \
        ["LSP vs DIP", "test doubles", "REST status codes", "mocking (stubs, fakes)"]
    assert split_areas("") == []


def test_requests_put_overdue_exams_first_and_skip_finals_and_answered():
    reqs = input_requests("SE", FACTS, [], tools=["Gradescope"])
    assert [r.component for r in reqs] == ["Midterm", "Participation"]
    assert reqs[0].likely_source == "Gradescope" and "no system shows a score" in reqs[0].reason
    assert reqs[1].likely_source == "iClicker / instructor records"
    answered = [UserEntry(course="SE", component="Midterm", item="Midterm", score=30, out_of=40)]
    assert [r.component for r in input_requests("SE", FACTS, answered)] == ["Participation"]
    feedback_only = [UserEntry(course="SE", component="Midterm", item="Midterm", improvement_areas=["LSP"])]
    assert "Midterm" in [r.component for r in input_requests("SE", FACTS, feedback_only)]  # still need the score


def test_store_replaces_corrections_and_rejects_bad_numbers(tmp_path):
    st = InputStore(tmp_path / "user_inputs.json")
    st.add(UserEntry(course="SE", component="Midterm", item="Midterm", score=28, out_of=40))
    st.add(UserEntry(course="SE", component="Midterm", item="Midterm", score=31, out_of=40, source="Gradescope"))
    assert [(e.score, e.source) for e in st.for_course("SE")] == [(31, "Gradescope")]
    with pytest.raises(ValueError):
        st.add(UserEntry(course="SE", component="Midterm", item="Midterm", score=400, out_of=40))
    with pytest.raises(ValueError):
        UserEntry(course="SE", component="Midterm", item="Midterm", score=10, out_of=0)
    assert json.loads((tmp_path / "user_inputs.json").read_text())["entries"][0]["source"] == "Gradescope"


def test_entries_change_the_grade_and_are_labelled():
    before = grade_summary("SE", VIEW)
    # Labs 18/20 = 90% (w35) only -> 90%
    assert before.current_pct == 90.0
    view, notes = apply_entries(VIEW, [UserEntry(course="SE", component="Midterm", item="Midterm", score=30, out_of=40,
                                                 source="Gradescope")])
    mid = view["groups"][2]["items"]
    assert mid == [["Midterm (entered by you, from Gradescope)", None, 40.0, "graded", 30.0, False]]  # placeholder replaced
    after = grade_summary("SE", view)
    # (35*0.9 + 25*0.75) / 60 = (31.5 + 18.75) / 60 = 83.75%
    assert after.current_pct == 83.75 and after.weight_determined == 60
    assert notes == ["Midterm: 30/40 entered by you (from Gradescope)"]
    assert needed_on_remaining(view, 80) is not None
    assert VIEW["groups"][2]["items"][0][4] is None  # input not mutated


def test_unknown_component_is_reported_not_guessed():
    _, notes = apply_entries(VIEW, [UserEntry(course="SE", component="Quizzes", item="Quiz 1", score=5, out_of=5)])
    assert notes == ["Skipped 'Quiz 1': no component named 'Quizzes'."]


def test_study_focus_links_areas_to_course_pages(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.json").write_text(json.dumps([
        {"id": "a", "url": "https://x/lectures/04a.pdf", "kind": "lecture", "title": "04a-test-doubles.pdf", "page": 14,
         "text": "Liskov substitution principle: subtypes must be substitutable for their base types."},
        {"id": "b", "url": "https://x/textbook/rest", "kind": "textbook", "title": "REST", "page": 1,
         "text": "REST status codes: 200 OK, 201 Created, 404 Not Found."}]))
    e = UserEntry(course="SE", component="Midterm", item="Midterm", improvement_areas=["Liskov substitution", "REST status codes"])
    f = study_focus([e], site, k=1)
    assert [(x["area"], x["materials"][0]["title"]) for x in f] == [("Liskov substitution", "04a-test-doubles.pdf"),
                                                                   ("REST status codes", "REST")]


def test_focus_query_keeps_distinctive_words():
    from canvas_agent.user_inputs import focus_query

    assert focus_query("recursion and its different types") == "recursion"
    assert focus_query("understanding code changes in design patterns") == "design patterns"
    assert focus_query("and the") == "and the"  # nothing distinctive: fall back to the original text


def test_entered_marks_reach_the_component_checks():
    """Regression: checks were built before entries were applied, so an entered midterm read "0 graded, no average"
    on the Grades tab while the course grade already counted it."""
    from canvas_agent.user_inputs import entries_into_checks

    checks = [{"component": "Midterm", "scored": 0, "avg_syllabus_rules": None, "items": []},
              {"component": "Labs", "scored": 1, "avg_syllabus_rules": 90.0,
               "items": [{"name": "Lab 1", "points": 10, "score": 9, "pct": 90.0}]}]
    view = {"name": "X", "weighted": True, "groups": [
        {"name": "Midterm", "weight": 40, "rules": {}, "items": [
            ["Midterm (entered by you, from Gradescope)", None, 40.0, "graded", 31.0, False]]},
        {"name": "Labs", "weight": 60, "rules": {}, "items": [["Lab 1", None, 10.0, "graded", 9.0, False]]}]}
    out = entries_into_checks(checks, view, "X")
    mid = out[0]
    assert mid["scored"] == 1 and mid["avg_syllabus_rules"] == 77.5
    assert mid["items"][0]["name"].endswith("(entered by you, from Gradescope)") and mid["items"][0]["pct"] == 77.5
    assert out[1] == checks[1]  # components without entries are untouched
    assert checks[0]["items"] == []  # input not mutated
    assert entries_into_checks(out, view, "X") == out  # idempotent: safe to apply again when reading old files
