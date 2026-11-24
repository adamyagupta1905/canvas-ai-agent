"""Where course data comes from. A Protocol so the real Canvas REST client and the
offline fixture source are interchangeable."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from canvas_agent.models import Assignment


class CourseSource(Protocol):
    def list_assignments(self, course_id: str) -> list[Assignment]: ...
    def get_syllabus_text(self, course_id: str) -> str: ...


class FixtureSource:
    """Reads a synthetic course from JSON on disk. Used by tests and demo mode."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def list_assignments(self, course_id: str) -> list[Assignment]:
        data = json.loads((self.root / course_id / "assignments.json").read_text())
        return [Assignment.model_validate(a) for a in data]

    def get_syllabus_text(self, course_id: str) -> str:
        return (self.root / course_id / "syllabus.txt").read_text()


# TODO(v1): CanvasRestSource using httpx + CANVAS_BASE_URL / CANVAS_TOKEN (read-only scopes).
