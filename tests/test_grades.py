"""Grade maths on synthetic courses shaped like real Canvas data (no real course data here).
Every expected value is worked out by hand in the comment next to it."""

from datetime import datetime, timezone

import pytest

from canvas_agent.grades import (
    compute_grade, grade_summary, needed_on_remaining, prefix_topic, term_progress,
    topic_mastery, tracker,
)

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)

WEIGHTED = {
    "name": "Synthetic Law", "weighted": True,
    "groups": [
        {"name": "Exercises", "weight": 10, "rules": {}, "items": [
            ["Class 01 Exercise", "2026-09-10T05:00:00Z", 5, "graded", 5, False],
            ["Class 02 Exercise", "2026-09-15T05:00:00Z", 5, "graded", 0, False],
            ["Class 03 Exercise", "2026-12-01T05:00:00Z", 5, "unsubmitted", None, False],
        ]},
        {"name": "Midterm", "weight": 40, "rules": {}, "items": [["Midterm", None, 100, "graded", 80, False]]},
        {"name": "Final", "weight": 50, "rules": {}, "items": []},
    ],
}

UNWEIGHTED = {
    "name": "Synthetic Comms", "weighted": False,
    "groups": [{"name": "Assignments", "weight": 0, "rules": {}, "items": [
        ["Essay", "2026-10-01T06:59:59Z", 50, "graded", 40, False],
        ["Quiz", "2026-10-07T06:59:59Z", 10, "pending_review", None, False],
        ["Speech", "2026-10-20T05:00:00Z", 40, "unsubmitted", None, False],
    ]}],
}


def test_current_grade_renormalises_over_scored_groups():
    s = grade_summary("LAW", WEIGHTED)
    # Exercises 5/10 = 50% (w10) -> 5.0; Midterm 80% (w40) -> 32.0; (5 + 32) / 50 = 74%
    assert s.current_pct == 74.0
    assert s.weight_determined == 50.0
    contrib = {g.name: g.contribution for g in s.groups}
    assert contrib == {"Exercises": 5.0, "Midterm": 32.0, "Final": None}


def test_canvas_final_scores_ungraded_as_zero_and_skips_empty_groups():
    # Exercises 5/15 = 33.33% (w10) -> 3.333; Midterm 32; Final has no items -> left out
    # (3.333 + 32) / 50 = 70.67%
    assert grade_summary("LAW", WEIGHTED).canvas_final_pct == 70.67


def test_floor_and_ceiling_cover_all_weight():
    s = grade_summary("LAW", WEIGHTED)
    # floor: Ex 5/15*10 = 3.333 + 32 + Final 0 = 35.33 ; ceiling: Ex 10/15*10 = 6.667 + 32 + 50 = 88.67
    assert (s.floor_pct, s.ceiling_pct) == (35.33, 88.67)


def test_needed_on_remaining_respects_partial_groups():
    # grade(x) = (5 + 5x)/15*10 + 32 + 50x = 35.333 + 53.333x ; 80 -> x = 0.8375
    assert needed_on_remaining(WEIGHTED, 80) == pytest.approx(83.75, abs=0.01)
    # 95 is beyond the ceiling (88.67): reported above 100 to show how far out of reach
    assert needed_on_remaining(WEIGHTED, 95) > 100


def test_drop_lowest_picks_the_best_group_percent():
    course = {"name": "Drops", "weighted": True, "groups": [
        {"name": "Polls", "weight": 100, "rules": {"drop_lowest": 2}, "items": [
            ["P1", None, 1, "graded", 0, False], ["P2", None, 1, "graded", 1, False],
            ["P3", None, 1, "graded", 0, False], ["P4", None, 1, "graded", 1, False],
        ]}]}
    r = compute_grade(course)
    assert r.pct == 100.0  # both zeros dropped
    assert sorted(r.groups[0].dropped) == ["P1", "P3"]


def test_drop_lowest_with_unequal_points_maximises_percent_not_lowest_raw_score():
    # Drop one of: A 9/10 (90%), B 1/2 (50%), C 40/100 (40%).
    # Dropping C gives 10/12 = 83.3%; dropping B gives 49/110 = 44.5%. Canvas drops C.
    course = {"name": "Uneq", "weighted": True, "groups": [
        {"name": "G", "weight": 100, "rules": {"drop_lowest": 1}, "items": [
            ["A", None, 10, "graded", 9, False], ["B", None, 2, "graded", 1, False],
            ["C", None, 100, "graded", 40, False]]}]}
    r = compute_grade(course)
    assert r.groups[0].dropped == ["C"]
    assert round(r.pct, 2) == 83.33


def test_drop_never_removes_every_item():
    course = {"name": "One", "weighted": True, "groups": [
        {"name": "G", "weight": 100, "rules": {"drop_lowest": 3}, "items": [["Only", None, 5, "graded", 2, False]]}]}
    assert compute_grade(course).pct == 40.0


def test_omitted_items_never_count_and_show_as_omitted():
    course = {"name": "Omit", "weighted": True, "omit_from_final": ["Practice"], "groups": [
        {"name": "G", "weight": 100, "rules": {}, "items": [
            ["Real", None, 10, "graded", 8, False], ["Practice", None, 10, "graded", 0, False]]}]}
    assert compute_grade(course).pct == 80.0
    rows = {r.name: r for r in tracker("OMIT", course, NOW)}
    assert rows["Practice"].status == "omitted"
    assert rows["Real"].weight_of_final == 100.0


def test_extra_credit_on_zero_point_item():
    course = {"name": "EC", "weighted": False, "groups": [
        {"name": "G", "weight": 0, "rules": {}, "items": [
            ["Test", None, 10, "graded", 7, False], ["Bonus", None, 0, "graded", 1, False]]}]}
    assert compute_grade(course).pct == 80.0  # (7 + 1) / 10


def test_unweighted_grade_uses_points_and_warns():
    s = grade_summary("COMMS", UNWEIGHTED)
    assert s.current_pct == 80.0  # only the scored essay counts: 40/50
    assert s.canvas_final_pct == 40.0  # 40 / (50 + 10 + 40)
    assert s.weight_determined is None
    assert any("syllabus weights" in w for w in s.warnings)


def test_canvas_check_flags_a_mismatch():
    course = dict(UNWEIGHTED, canvas_totals={"current": 80.0, "final": 40.0})
    assert grade_summary("C", course).canvas_check["match"] is True
    course = dict(UNWEIGHTED, canvas_totals={"current": 81.0, "final": 40.0})
    s = grade_summary("C", course)
    assert s.canvas_check["match"] is False
    assert any("do not match" in w for w in s.warnings)


def test_tracker_statuses_and_sizes():
    rows = {r.name: r for r in tracker("LAW", WEIGHTED, NOW)}
    assert rows["Class 01 Exercise"].status == "graded"
    assert rows["Class 03 Exercise"].status == "upcoming"
    assert rows["Class 01 Exercise"].weight_of_final == round(10 * 5 / 15, 3)
    assert rows["Class 01 Exercise"].size == "minor"
    assert rows["Midterm"].size == "major"

    urows = {r.name: r for r in tracker("COMMS", UNWEIGHTED, NOW)}
    assert urows["Quiz"].status == "awaiting_grade"
    assert urows["Speech"].status == "past_due_unsubmitted"
    assert urows["Essay"].weight_of_final is None  # unknown without syllabus weights


def test_term_progress_and_topic_mastery():
    rows = tracker("LAW", WEIGHTED, NOW)
    assert term_progress(rows, NOW) == 66.7  # 2 of 3 dated items are past
    m = topic_mastery(rows, prefix_topic(r"Class (\d+)"))
    assert m == {"01": 100.0, "02": 0.0}
