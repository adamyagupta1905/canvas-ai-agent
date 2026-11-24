"""PrairieLearn connector on a synthetic snapshot shaped like the real pages. No network, no session."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from canvas_agent.grades import grade_summary
from canvas_agent.prairielearn import merge_into_canvas, normalise, parse_credit, summarise
from canvas_agent.tools import Store, build_tools

TZ = ZoneInfo("America/Vancouver")
COLLECTED = datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc)  # evening of Nov 23 in Vancouver


def A(set_, label, title, score, points, credit="", open_=False, questions=()):
    return {"set": set_, "label": label, "title": title, "credit_text": credit, "score_text": score,
            "points": points, "open": open_, "questions": [list(q) for q in questions]}


SNAP = {"collected_at": "2026-11-24T06:00:00Z", "timezone": "America/Vancouver", "courses": [{
    "course_instance_id": "1", "title": "SYN 330: Synthetic Machine Learning, 2026F", "assessments": [
        A("Homeworks", "HW1", "Homework 1", "86%", [43, 50], questions=[("Question 1", "complete ; 43 /50")]),
        A("Homeworks", "HW2", "Homework 2", "Score not shown", [24, 24]),
        A("Homeworks", "HW3", "Homework 3", "0%", [0, 38], credit="100% until today, 11:59pm", open_=True,
          questions=[("Question 1", "saved for manual grading ; pending /38")]),
        A("Homeworks", "HW4", "Homework 4", "Not started", None, credit="100% until Fri, Dec 4, 11:59pm"),
        A("Homeworks", "HW5", "Homework 5", "36%", [7, 19], questions=[("Q1", "4 /4"), ("Q2", "pending /10"), ("Q3", "3 /5")]),
        A("Midterms", "MT1", "Midterm 1", "78%", [22, 28]),
        A("Labs", "LAB2", "Lab 2: Redo", "Not started", [0, 17], open_=True),
        A("Practice Assessments", "P-MT1", "Midterm 1 Practice", "New instance", None, credit="100%"),
        A("Practice Assessments", "P-MT1#1", "Midterm 1 Practice instance #1", "23%", [7, 30]),
        A("Tutorials", "TuP3", "Final Exam Prep", "Not started", None, credit="Available Sat, Dec 5, start of day"),
        A("Labs", "LAB6", "Lab 6", "0%", [0, 15], credit="None", open_=True, questions=[("Q1", "Open ; — /15")]),
        A("Labs", "LAB9", "Lab 9", "0%", [0, 5], credit="100% until Thu, Nov 26, end of day", open_=True,
          questions=[("Q1", "— /1")]),
        A("Deliverables", "DELIV1", "Design Analysis", "0%", [0, 8], questions=[("Q1", "— /2")]),
    ]}]}


def by_label():
    return {a.label: a for a in normalise(SNAP)}


def test_credit_text_variants_parse_in_course_time_zone():
    assert parse_credit("100% until today, 11:59pm", COLLECTED, TZ)[1] == datetime(2026, 11, 23, 23, 59, tzinfo=TZ)
    assert parse_credit("100% until Thu, Nov 26, end of day", COLLECTED, TZ)[1] == datetime(2026, 11, 26, 23, 59, 59, tzinfo=TZ)
    assert parse_credit("100% until Fri, Nov 27, 6:05pm", COLLECTED, TZ)[1] == datetime(2026, 11, 27, 18, 5, tzinfo=TZ)
    assert parse_credit("Available Sat, Dec 5, start of day", COLLECTED, TZ) == (None, None, datetime(2026, 12, 5, 0, 0, tzinfo=TZ))
    assert parse_credit("None", COLLECTED, TZ) == (0.0, None, None)
    assert parse_credit("100%", COLLECTED, TZ) == (100.0, None, None)
    assert parse_credit("100% until Mon, Jan 11, end of day", COLLECTED, TZ)[1].year == 2027  # January seen in November


def test_statuses_and_kinds():
    a = by_label()
    assert a["HW1"].course == "SYN330" and (a["HW1"].status, a["HW1"].pct) == ("graded", 86.0)
    assert (a["HW2"].status, a["HW2"].pct) == ("score_hidden", 100.0)  # points visible although score hidden
    assert a["HW3"].status == "awaiting_grade" and a["HW3"].pending_points == 38  # 0% but submitted
    assert a["HW4"].status == "not_started" and a["HW4"].deadline is not None
    assert (a["HW5"].status, a["HW5"].pending_questions, a["HW5"].pending_points) == ("partially_graded", 1, 10)
    assert a["LAB2"].kind == "redo"
    assert a["P-MT1"].kind == "practice_template" and a["P-MT1#1"].kind == "practice"
    assert a["TuP3"].status == "not_yet_available"
    assert a["LAB6"].credit_pct == 0.0 and a["LAB6"].status == "open_not_done"
    assert a["DELIV1"].status == "closed_no_credit"


CANVAS = {"name": "Synthetic ML", "weighted": True, "groups": [
    {"name": "HW", "weight": 50, "rules": {}, "items": [
        ["pct_hw1", None, 100, "unsubmitted", None, False],
        ["pct_hw2", None, 100, "graded", 90, False],          # disagrees with PrairieLearn (100%): conflict
        ["pct_hw3", None, 100, "unsubmitted", None, False],   # PrairieLearn awaiting grade: stays empty
        ["pct_hw5", None, 100, "unsubmitted", None, False]]},  # partially graded: not filled
    {"name": "SYN 330 Midterm 1", "weight": 50, "rules": {}, "items": []}]}


def test_merge_fills_gaps_adds_missing_exams_and_never_overwrites():
    merged, notes = merge_into_canvas(CANVAS, normalise(SNAP))
    acts = {(n.canvas_item or n.canvas_group, n.action) for n in notes}
    assert ("pct_hw1", "filled") in acts and ("pct_hw2", "conflict") in acts and ("SYN 330 Midterm 1", "added") in acts
    items = {it[0]: it for g in merged["groups"] for it in g["items"]}
    assert items["pct_hw1"][4] == 86.0
    assert items["pct_hw2"][4] == 90  # Canvas kept
    assert items["pct_hw3"][4] is None and items["pct_hw5"][4] is None
    assert items["Midterm 1"][2:5] == [28.0, "graded", 22.0]
    assert CANVAS["groups"][0]["items"][0][4] is None  # input not mutated
    # HW (86 + 90) / 200 = 88% (w50) ; MT1 22/28 = 78.571% (w50) -> 83.29%
    assert grade_summary("SYN330", merged).current_pct == 83.29


def test_summary_lists_pending_deadlines_and_openings():
    now = datetime(2026, 11, 23, 12, 0, tzinfo=TZ)
    s = summarise(normalise(SNAP), "SYN330", now)
    assert {x["label"] for x in s["awaiting_grade"]} == {"HW3", "HW5"}
    assert [d["label"] for d in s["credit_deadlines"]] == ["HW3", "LAB9", "HW4"]
    assert s["not_yet_available"][0]["label"] == "TuP3"
    hw = next(x for x in s["sets"] if x["set"] == "Homeworks")
    assert hw["average_pct"] == round(100 * (43 + 24) / (50 + 24), 2)  # graded work only


def test_store_merges_prairielearn_and_tools_see_it(tmp_path):
    (tmp_path / "canvas_snapshot.json").write_text(json.dumps({"courses": {"SYN330": CANVAS}}))
    (tmp_path / "prairielearn_snapshot.json").write_text(json.dumps(SNAP))
    tools = build_tools(Store(tmp_path), now=datetime(2026, 11, 23, 12, 0, tzinfo=TZ))
    assert tools["course_grade"].fn("SYN330")["current_pct"] == 83.29
    up = tools["upcoming"].fn("SYN330", days=14)
    assert [u["item"].split(":")[0] for u in up if u["source"] == "PrairieLearn"] == ["HW3", "LAB9", "HW4"]
    st = tools["prairielearn_status"].fn("SYN330")
    assert any(n["action"] == "conflict" for n in st["merged_into_canvas"])


def test_canvas_total_check_is_moved_aside_after_a_merge():
    course = dict(CANVAS, canvas_totals={"current": 90.0, "final": 30.0})
    merged, _ = merge_into_canvas(course, normalise(SNAP))
    assert "canvas_totals" not in merged and merged["canvas_totals_canvas_only"]["current"] == 90.0
    assert grade_summary("SYN330", merged).canvas_check is None
