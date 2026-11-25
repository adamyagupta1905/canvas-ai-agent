"""The committed fictional demo (demo_data/) must keep working: it is what visitors see without a key."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from canvas_agent import course_view as cv
from canvas_agent.tools import Store

DEMO = Path(__file__).resolve().parent.parent / "demo_data"


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(cv, "local_tz", lambda: datetime.fromisoformat("2026-11-24T10:00:00-08:00").tzinfo)
    return Store(DEMO), datetime.fromisoformat(json.loads((DEMO / "demo_meta.json").read_text())["as_of"])


def test_demo_courses_render_every_view(demo):
    store, now = demo
    rows = {r["code"]: r for r in cv.overview(store, now)}
    assert set(rows) == {"CS101", "LAW201"}
    # CS101: labs 57/60 after dropping the 0 (x10 = 9.5), project equal-weight 80% (x30 = 24), midterm 78% (x20 = 15.6)
    assert rows["CS101"]["grade_pct"] == 81.83 and rows["CS101"]["graded_weight_pct"] == 60
    for code in rows:
        assert cv.notes(store, code, now) and cv.deadlines(store, code, now) and cv.plan(store, code, now)["days"]


def test_demo_merges_the_same_deadline_from_two_sources(demo):
    store, now = demo
    d = cv.deadlines(store, "CS101", now)
    lab8 = [x for x in d if "Lab 8" in x["what"]]
    assert len(lab8) == 1 and sorted(lab8[0]["sources"]) == ["Canvas", "course website"]


def test_demo_contains_no_real_course_data():
    """Forbidden terms (real course codes, school names) live in data/private_terms.txt, which is gitignored, so the
    list itself never reaches the repo. Without that file (e.g. in CI) the check is skipped."""
    terms_file = DEMO.parent / "data" / "private_terms.txt"
    if not terms_file.exists():
        pytest.skip("no private term list on this machine")
    terms = [t.strip() for t in terms_file.read_text().splitlines() if t.strip() and not t.startswith("#")]
    text = " ".join(p.read_text(errors="ignore") for p in DEMO.rglob("*") if p.is_file() and p.suffix != ".pdf")
    import re

    # case-sensitive whole words: catches a real course code or school domain, not ordinary words that contain it
    assert not [t for t in terms if re.search(rf"\b{re.escape(t)}\b", text)]
