"""PDF -> page text, with OCR for pages that have no usable text layer.

Stage 1-2 of the standard document flow (see docs/SYLLABUS_FLOW.md):
  1. Read the embedded text layer of each page (pypdf). Fast and exact when present.
  2. If a page has fewer than `min_chars` characters (scanned page, image of a table), render it
     (pypdfium2, no system poppler needed) and OCR it with Tesseract.
Every page records which method produced its text, so later stages can tell OCR text apart.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel

OcrFn = Callable[["object"], str]  # PIL.Image -> text


class PageText(BaseModel):
    page: int  # 1-based
    text: str
    method: Literal["text_layer", "ocr", "empty", "html"]


def tesseract_ocr(image) -> str:
    import pytesseract

    return pytesseract.image_to_string(image)


def _render(pdf_path: Path, page_index: int, dpi: int):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return pdf[page_index].render(scale=dpi / 72).to_pil()
    finally:
        pdf.close()


def clean_text(text: str) -> str:
    """Normalise whitespace and common PDF artefacts without changing words."""
    text = text.replace("­", "")  # soft hyphen
    text = re.sub(r"-\n(?=[a-z])", "", text)  # words hyphenated across lines
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_pages(html: str) -> list[PageText]:
    """A syllabus typed straight into Canvas (no PDF) becomes one 'page' for the same flow."""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}

        def __init__(self):
            super().__init__()
            self.out: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag in self.BLOCK:
                self.out.append("\n\n" if tag in {"p", "h1", "h2", "h3", "h4", "table"} else "\n")
            elif tag in {"td", "th"}:
                self.out.append(" ")

        def handle_data(self, data):
            self.out.append(data)

    p = _P()
    p.feed(html or "")
    text = clean_text("".join(p.out))
    return [PageText(page=1, text=text, method="html")] if text else []


def extract_pages(
    pdf_path: Path | str,
    min_chars: int = 50,
    ocr: OcrFn | None = tesseract_ocr,
    dpi: int = 300,
) -> list[PageText]:
    from pypdf import PdfReader

    pdf_path = Path(pdf_path)
    reader = PdfReader(str(pdf_path))
    pages: list[PageText] = []
    for i, page in enumerate(reader.pages):
        text = clean_text(page.extract_text() or "")
        if len(text) >= min_chars:
            pages.append(PageText(page=i + 1, text=text, method="text_layer"))
            continue
        if ocr is not None:
            ocr_text = clean_text(ocr(_render(pdf_path, i, dpi)))
            if ocr_text:
                pages.append(PageText(page=i + 1, text=ocr_text, method="ocr"))
                continue
        pages.append(PageText(page=i + 1, text=text, method="empty"))
    return pages
