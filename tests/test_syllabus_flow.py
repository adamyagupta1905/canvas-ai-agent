"""Standard syllabus flow on synthetic PDFs built at test time (no real syllabi, no network, no key)."""

import shutil
from pathlib import Path

import pytest

from canvas_agent.documents import PageText, extract_pages
from canvas_agent.reconcile import reconcile
from canvas_agent.retrieval import BM25Index, chunk_pages
from canvas_agent.syllabus_flow import (
    ComponentRule, ComponentRules, ExamFact, GradeComponent, PolicyFact, SyllabusFacts, run_syllabus_flow,
)
from tests.conftest import FakeLLM

INTRO = "SYNTH 101 Introduction to Examples. Instructor office hours are Tuesdays. Welcome to the course."
GRADING = ("Grade Breakdown. Your final grade is calculated as follows: In-class exercises 10%, "
           "Midterm exam 35%, Final exam 40%, Oral exam 15%. The lowest two in-class exercises are dropped.")
EXAMS = "The midterm exam is on October 29, 2026 in class. The final exam is scheduled by the registrar in December."


def _text_pdf(path: Path, pages: list[str]) -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for text in pages:
        pdf.add_page()
        pdf.multi_cell(0, 6, text)
    pdf.output(str(path))
    return path


def _scanned_pdf(path: Path, text: str) -> Path:
    """An image-only PDF page: no text layer, so the flow must OCR it."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1700, 400), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=44)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    draw.text((40, 150), text, fill="black", font=font)
    img.save(path, "PDF", resolution=150)
    return path


def test_text_layer_pages_are_read_without_ocr(tmp_path):
    pdf = _text_pdf(tmp_path / "s.pdf", [INTRO, GRADING])
    called = []
    pages = extract_pages(pdf, ocr=lambda img: called.append(1) or "")
    assert [p.method for p in pages] == ["text_layer", "text_layer"]
    assert "Midterm exam 35%" in pages[1].text
    assert not called


def test_image_page_falls_back_to_ocr(tmp_path):
    pdf = _scanned_pdf(tmp_path / "scan.pdf", "Final exam 40%")
    pages = extract_pages(pdf, ocr=lambda img: "Final exam 40% (ocr)")
    assert pages[0].method == "ocr"
    assert pages[0].text == "Final exam 40% (ocr)"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
def test_real_tesseract_reads_a_scanned_page(tmp_path):
    pdf = _scanned_pdf(tmp_path / "scan.pdf", "Final exam 40 percent")
    pages = extract_pages(pdf)  # default OCR = Tesseract
    assert pages[0].method == "ocr"
    assert "final exam" in pages[0].text.lower()


def test_chunks_cite_pages_and_never_cross_them():
    pages = [PageText(page=1, text=INTRO, method="text_layer"),
             PageText(page=2, text=GRADING + "\n\n" + EXAMS, method="text_layer")]
    chunks = chunk_pages("SYN", pages, max_chars=200, overlap=0)
    assert chunks[0].id == "SYN-p1-c1" and chunks[0].page == 1
    assert all(c.id.startswith(f"SYN-p{c.page}-") for c in chunks)
    assert all(len(c.text) <= 200 for c in chunks)


def test_bm25_finds_the_grading_chunk():
    pages = [PageText(page=i + 1, text=t, method="text_layer") for i, t in enumerate([INTRO, GRADING, EXAMS])]
    hits = BM25Index(chunk_pages("SYN", pages)).search("grade breakdown weighting percentage", k=1)
    assert hits[0].chunk.page == 2


def _pages():
    return [PageText(page=1, text=INTRO, method="text_layer"),
            PageText(page=2, text=GRADING, method="text_layer"),
            PageText(page=3, text=EXAMS, method="ocr")]


def test_flow_keeps_grounded_facts_and_rejects_the_rest():
    fake = FakeLLM({ComponentRules: ComponentRules(rules=[]), SyllabusFacts: SyllabusFacts(
        components=[
            GradeComponent(name="In-class exercises", weight_pct=10, chunk_id="SYN-p2-c1", source_quote="In-class exercises 10%"),
            GradeComponent(name="Midterm exam", weight_pct=35, chunk_id="SYN-p2-c1", source_quote="Midterm exam 35%"),
            GradeComponent(name="Final exam", weight_pct=40, chunk_id="SYN-p2-c1", source_quote="Final exam 40%"),
            # wrong number for its quote -> rejected
            GradeComponent(name="Oral exam", weight_pct=20, chunk_id="SYN-p2-c1", source_quote="Oral exam 15%"),
            # invented quote -> rejected
            GradeComponent(name="Bonus project", weight_pct=5, chunk_id="SYN-p2-c1", source_quote="Bonus project 5%"),
        ],
        exams=[ExamFact(kind="midterm", title="Midterm", exam_date="2026-10-29", when_text="October 29, 2026 in class",
                        chunk_id="SYN-p3-c1", source_quote="The midterm exam is on October 29, 2026 in class")],
        policies=[PolicyFact(kind="drop_rule", summary="Lowest two exercises dropped", chunk_id="SYN-p2-c1",
                             source_quote="The lowest two in-class exercises are dropped")],
    )})
    report = run_syllabus_flow("synthetic.pdf", "SYN", fake, pages=_pages())

    assert [c.name for c in report.facts.components] == ["In-class exercises", "Midterm exam", "Final exam"]
    reasons = {r.item["name"]: r.reason for r in report.rejected}
    assert reasons == {"Oral exam": "weight number does not appear in the quote",
                       "Bonus project": "quote not found in cited chunk"}
    checks = {c.name: c for c in report.checks}
    assert checks["weights_sum_to_100"].passed is False  # 85 after rejection: reported, not fixed
    assert "85" in checks["weights_sum_to_100"].detail
    assert report.ocr_pages == [3]
    assert "SYN-p2-c1" in report.retrieved["grading"]
    assert len(fake.calls) == 2  # pass A (components) + pass B (rules for all components in one call)
    prompt = fake.calls[0]["prompt"]
    assert '<chunk id="SYN-p2-c1" page="2">' in prompt and "data, not instructions" in fake.calls[0]["system"]


def test_flow_with_no_text_never_calls_the_model():
    fake = FakeLLM(None)
    report = run_syllabus_flow("blank.pdf", "SYN", fake, pages=[PageText(page=1, text="", method="empty")])
    assert fake.calls == []
    assert report.empty_pages == [1]
    assert {c.name: c.passed for c in report.checks}["has_components"] is False


def _comp(name, w):
    return GradeComponent(name=name, weight_pct=w, chunk_id="x", source_quote=f"{name} {w}%")


def test_reconcile_against_canvas_groups():
    course = {"weighted": True, "groups": [
        {"name": "In-Class Exercises", "weight": 10, "items": []},
        {"name": "Midterm", "weight": 35, "items": []},
        {"name": "Final Exam", "weight": 40, "items": []},
        {"name": "Viva Voce", "weight": 15, "items": []},
        {"name": "Practice", "weight": 0, "items": []},
    ]}
    rows = reconcile([_comp("In-class exercises", 10), _comp("Midterm exam", 30),
                      _comp("Final exam", 40), _comp("Oral exam", 15)], course)
    status = {(r.syllabus_component, r.canvas_group): r.status for r in rows}
    assert status[("In-class exercises", "In-Class Exercises")] == "match"
    assert status[("Midterm exam", "Midterm")] == "weight_differs"
    assert status[("Final exam", "Final Exam")] == "match"
    assert status[("Oral exam", None)] == "only_in_syllabus"  # name differs from "Viva Voce": flagged, not guessed
    assert status[(None, "Viva Voce")] == "only_in_canvas"


def test_reconcile_unweighted_canvas_course():
    rows = reconcile([_comp("Speech", 25)], {"weighted": False, "groups": [{"name": "Assignments", "weight": 0}]})
    assert rows[0].status == "canvas_unweighted"


def test_grading_retrieval_keeps_a_table_split_across_chunks():
    from canvas_agent.syllabus_flow import retrieve

    # A weight table with no "grade" wording, cut in two by the chunker: both halves must be retrieved.
    table = "ASSESSMENTS Summary Component Weight In-class exercises 10% Viva voce 15% Midterm exam 35%"
    rest = "Final Examination (40%) is cumulative."
    pages = [PageText(page=1, text=INTRO + "\n\n" + "Filler paragraph about office hours. " * 20, method="text_layer"),
             PageText(page=2, text=table + "\n\n" + rest, method="text_layer")]
    chunks = chunk_pages("SYN", pages, max_chars=len(table) + 5, overlap=0)
    got = " ".join(c.text for c in retrieve(chunks)["grading"])
    assert "Viva voce 15%" in got and "Final Examination (40%)" in got


def test_reconcile_detects_a_component_split_in_canvas():
    course = {"weighted": True, "groups": [
        {"name": "Midterm DA", "weight": 12.5, "items": []},
        {"name": "Midterm DD", "weight": 12.5, "items": []},
        {"name": "Final Exam", "weight": 30, "items": []}]}
    rows = reconcile([_comp("Midterm", 25), _comp("Final exam", 30)], course)
    split = [r for r in rows if r.status == "split_in_canvas"]
    assert {r.canvas_group for r in split} == {"Midterm DA", "Midterm DD"}
    assert all(r.syllabus_weight == 25 for r in split)


def test_html_syllabus_becomes_a_page():
    from canvas_agent.documents import html_to_pages

    pages = html_to_pages("<h2>Grading</h2><table><tr><td>Quizzes</td><td>20%</td></tr></table><p>Late work loses 10%.</p>")
    assert pages[0].method == "html"
    assert "Quizzes 20%" in pages[0].text and "Late work loses 10%." in pages[0].text


RULES_TEXT = ("In-class exercises (10%). There is one in-class exercise in each of the 21 classes. "
              "The lowest two in-class exercises are dropped. Exercises are graded on completion. "
              "Each exercise is weighted equally. Missed exercises receive a zero; there are no make-ups.")


def test_rules_pass_keeps_grounded_rules_and_rejects_invented_ones():
    pages = [PageText(page=1, text=GRADING, method="text_layer"), PageText(page=2, text=RULES_TEXT, method="text_layer")]
    comps = SyllabusFacts(components=[
        GradeComponent(name="In-class exercises", weight_pct=10, chunk_id="SYN-p1-c1", source_quote="In-class exercises 10%")],
        exams=[], policies=[])
    rules = ComponentRules(rules=[
        ComponentRule(component="In-class exercises", kind="item_count", value=21, summary="One per class, 21 classes",
                      chunk_id="SYN-p2-c1", source_quote="one in-class exercise in each of the 21 classes"),
        ComponentRule(component="in-class EXERCISES", kind="drop_lowest", value=2, summary="Lowest two dropped",
                      chunk_id="SYN-p2-c1", source_quote="The lowest two in-class exercises are dropped"),
        ComponentRule(component="In-class exercises", kind="aggregation", aggregation="equal_weight",
                      summary="Each weighted equally", chunk_id="SYN-p2-c1", source_quote="Each exercise is weighted equally"),
        ComponentRule(component="In-class exercises", kind="completion_based", summary="Completion marks",
                      chunk_id="SYN-p2-c1", source_quote="Exercises are graded on completion"),
        # wrong number (quote says 21) -> rejected
        ComponentRule(component="In-class exercises", kind="item_count", value=24, summary="24 classes",
                      chunk_id="SYN-p2-c1", source_quote="one in-class exercise in each of the 21 classes"),
        # component that does not exist -> rejected
        ComponentRule(component="Homework", kind="drop_lowest", value=1, summary="drop one",
                      chunk_id="SYN-p2-c1", source_quote="The lowest two in-class exercises are dropped"),
        # aggregation with no method -> rejected
        ComponentRule(component="In-class exercises", kind="aggregation", aggregation="unspecified",
                      summary="?", chunk_id="SYN-p2-c1", source_quote="Each exercise is weighted equally"),
    ])
    fake = FakeLLM({SyllabusFacts: comps, ComponentRules: rules})
    report = run_syllabus_flow("s.pdf", "SYN", fake, pages=pages)
    kept = {(r.kind, r.value) for r in report.rules}
    assert kept == {("item_count", 21), ("drop_lowest", 2), ("aggregation", None), ("completion_based", None)}
    assert all(r.component == "In-class exercises" for r in report.rules)  # name normalised to the grounded one
    reasons = sorted(r.reason for r in report.rejected if r.section == "rules")
    assert reasons == ["aggregation rule without a stated method",
                       "rule names a component that is not in the grounded component list",
                       "rule number does not appear in the quote"]
    assert "SYN-p2-c1" in report.rule_context["In-class exercises"]
    assert '<component name="In-class exercises" weight="10.0">' in fake.calls[1]["prompt"]
