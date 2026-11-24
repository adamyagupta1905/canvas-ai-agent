"""Pydantic models shared across modules. These are the contracts between
the Canvas source, the LLM steps and the deterministic progress code."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class SubmissionState(str, Enum):
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    GRADED = "graded"


class Assignment(BaseModel):
    id: str
    course_id: str
    name: str
    due_at: datetime | None = None  # missing due date stays missing
    points_possible: float | None = None
    score: float | None = None
    state: SubmissionState = SubmissionState.NOT_SUBMITTED


class ExamEvent(BaseModel):
    """An exam found in a syllabus. Extracted by the LLM, never invented."""

    kind: str = Field(description="midterm, final, or quiz")
    title: str
    exam_date: date | None = Field(
        default=None, description="Null if the syllabus does not state a date."
    )
    source_quote: str = Field(description="Exact syllabus text the date came from.")


class SyllabusExtraction(BaseModel):
    exams: list[ExamEvent]


class QuizQuestion(BaseModel):
    id: str
    question: str
    choices: list[str]
    correct_index: int
    source_ref: str = Field(description="Which lecture/module the question is grounded in.")


class CourseProgress(BaseModel):
    """Computed by Python in progress.py; never by the model."""

    course_id: str
    total: int
    completed: int
    completion_pct: float | None  # None when there are no assignments
    overdue: list[str]
    next_due: str | None
