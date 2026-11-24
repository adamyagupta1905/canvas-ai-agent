from datetime import datetime

from canvas_agent.models import QuizQuestion
from canvas_agent.progress import course_progress, mark_quiz
from canvas_agent.sources import FixtureSource


def test_course_progress_from_fixture(fixtures_root):
    assignments = FixtureSource(fixtures_root).list_assignments("ECON101")
    p = course_progress("ECON101", assignments, now=datetime(2026, 10, 5))
    assert p.total == 5
    assert p.completed == 2
    assert p.completion_pct == 40.0
    assert p.overdue == ["Essay Draft"]
    assert p.next_due == "Problem Set 3"  # undated assignment is never "next due"


def test_course_progress_empty_course_is_none_not_zero():
    p = course_progress("NONE", [], now=datetime(2026, 10, 5))
    assert p.completion_pct is None


def _q(i, correct):
    return QuizQuestion(id=i, question="?", choices=["a", "b", "c"], correct_index=correct, source_ref="wk1")


def test_mark_quiz_counts_unanswered_separately():
    qs = [_q("q1", 0), _q("q2", 1), _q("q3", 2)]
    r = mark_quiz(qs, {"q1": 0, "q2": 2})
    assert r["correct"] == 1 and r["total"] == 3
    assert r["score_pct"] == 33.3
    assert r["wrong"] == ["q2"]
    assert r["unanswered"] == ["q3"]
