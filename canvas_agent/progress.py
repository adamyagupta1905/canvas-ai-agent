"""Deterministic progress maths. The LLM never computes these numbers."""

from __future__ import annotations

from datetime import datetime

from canvas_agent.models import Assignment, CourseProgress, QuizQuestion, SubmissionState


def course_progress(course_id: str, assignments: list[Assignment], now: datetime) -> CourseProgress:
    mine = [a for a in assignments if a.course_id == course_id]
    done = [a for a in mine if a.state != SubmissionState.NOT_SUBMITTED]
    open_ = [a for a in mine if a.state == SubmissionState.NOT_SUBMITTED]

    overdue = [a.name for a in open_ if a.due_at is not None and a.due_at < now]
    upcoming = sorted(
        (a for a in open_ if a.due_at is not None and a.due_at >= now),
        key=lambda a: a.due_at,
    )
    pct = round(100 * len(done) / len(mine), 1) if mine else None

    return CourseProgress(
        course_id=course_id,
        total=len(mine),
        completed=len(done),
        completion_pct=pct,
        overdue=overdue,
        next_due=upcoming[0].name if upcoming else None,
    )


def mark_quiz(questions: list[QuizQuestion], answers: dict[str, int]) -> dict:
    """Mark multiple-choice answers. Unanswered questions count as wrong and are listed."""
    correct = [q.id for q in questions if answers.get(q.id) == q.correct_index]
    unanswered = [q.id for q in questions if q.id not in answers]
    total = len(questions)
    return {
        "correct": len(correct),
        "total": total,
        "score_pct": round(100 * len(correct) / total, 1) if total else None,
        "wrong": [q.id for q in questions if q.id not in correct and q.id not in unanswered],
        "unanswered": unanswered,
    }
