from datetime import date

from canvas_agent.models import ExamEvent, SyllabusExtraction
from canvas_agent.sources import FixtureSource
from canvas_agent.syllabus import extract_exams
from tests.conftest import FakeLLM


def test_ungrounded_exam_is_dropped(fixtures_root):
    text = FixtureSource(fixtures_root).get_syllabus_text("ECON101")
    fake = FakeLLM(SyllabusExtraction(exams=[
        ExamEvent(kind="midterm", title="Midterm Exam", exam_date=date(2026, 10, 8),
                  source_quote="The Midterm Exam will be held in class on October 8, 2026."),
        ExamEvent(kind="final", title="Final Exam", exam_date=date(2026, 12, 15),
                  source_quote="Final Exam on December 15"),  # invented: not in syllabus
    ]))
    result = extract_exams(fake, text)
    assert [e.kind for e in result.exams] == ["midterm"]
    assert "<syllabus>" in fake.calls[0]["prompt"]
    assert "data, not instructions" in fake.calls[0]["system"]
