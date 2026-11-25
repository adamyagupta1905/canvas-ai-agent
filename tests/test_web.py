"""Orbit web UI: the exporter adds band/summary/timeline in Python, and the local server serves the page + JSON."""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from canvas_agent import course_view as cv
from scripts import export_dashboard as ex
from scripts import serve_web

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo_data"


@pytest.fixture(autouse=True)
def demo_tz(monkeypatch):
    monkeypatch.setattr(cv, "local_tz", lambda: datetime.fromisoformat("2026-11-24T10:00:00-08:00").tzinfo)


def test_band_thresholds_and_missing_grade():
    assert [ex.band(x) for x in (90, 75, 74.99, 65, 64.99, None)] == ["safe", "safe", "watch", "watch", "at_risk", None]


def test_demo_export_has_summary_and_timeline():
    now = datetime.fromisoformat(json.loads((DEMO / "demo_meta.json").read_text())["as_of"])
    out = ex.export(now, DEMO)
    grades = {c["code"]: c["grade"]["current_pct"] for c in out["courses"]}
    assert out["summary"]["average_current_pct"] == round(sum(grades.values()) / len(grades), 2)
    assert {c["code"]: c["band"] for c in out["courses"]} == {"CS101": "safe", "LAW201": "safe"}
    tl = out["timeline"]
    assert tl and all(0 <= d["days_left"] <= 14 and d["code"] in grades for d in tl)
    assert [d["when"] for d in tl] == sorted(d["when"] for d in tl)


def test_unweighted_points_total_gets_no_band_or_average(monkeypatch, tmp_path):
    snap = {"courses": {"X1": {"name": "Points course", "weighted": False, "groups": [
        {"name": "Assignments", "weight": 0, "rules": {}, "items": [["A1", None, 10, "graded", 9, False]]}]}}}
    (tmp_path / "canvas_snapshot.json").write_text(json.dumps(snap))
    out = ex.export(datetime.fromisoformat("2026-11-24T18:00:00+00:00"), tmp_path)
    c = out["courses"][0]
    assert c["grade_is_course_grade"] is False and c["band"] is None
    assert out["summary"]["average_current_pct"] is None and out["summary"]["graded_courses"] == 0


def test_data_dir_falls_back_to_demo_with_frozen_now(monkeypatch, tmp_path):
    monkeypatch.setenv("COURSE_DATA_DIR", str(tmp_path / "missing"))
    d, now, demo = ex.data_dir_and_now(ROOT)
    assert d == DEMO and demo and now.isoformat() == "2026-11-24T18:00:00+00:00"


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("COURSE_DATA_DIR", str(DEMO))
    srv = serve_web.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_server_binds_localhost_only():
    srv = serve_web.make_server(0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_server_serves_page_and_fresh_json(server):
    page = urllib.request.urlopen(server + "/").read().decode()
    assert "app.js" in page and "Semester Orbit" in page
    r = urllib.request.urlopen(server + "/dashboard.json")
    assert r.headers["Cache-Control"] == "no-store" and "default-src 'self'" in r.headers["Content-Security-Policy"]
    data = json.loads(r.read())
    assert data["demo"] is True and {c["code"] for c in data["courses"]} == {"CS101", "LAW201"}


def test_server_rejects_writes(server):
    req = urllib.request.Request(server + "/dashboard.json", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 501


def test_ui_inserts_course_text_as_text_only():
    js = (ROOT / "web" / "app.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "eval(" not in js


def test_note_scores_are_item_pct_pairs():
    """web/app.js renders note scores as `item pct`; keep the exporter shape it relies on."""
    now = datetime.fromisoformat(json.loads((DEMO / "demo_meta.json").read_text())["as_of"])
    scores = [x for c in ex.export(now, DEMO)["courses"] for n in c["notes"] for x in n["scores"]]
    assert scores and all(set(x) == {"item", "pct"} for x in scores)


def test_entered_marks_reach_the_component_table():
    """Regression: component checks predate the student's entries, so an entered midterm showed as ungraded."""
    from canvas_agent.grades import GroupResult

    comp = {"component": "Midterm", "graded": 0, "average_pct": None,
            "items": [{"name": "Midterm (not yet in Canvas)", "pct": None, "status": "not graded yet"}]}
    group = {"name": "Midterm", "items": [["Midterm (entered by you, from Gradescope)", None, 40.0, "graded", 31.0, False]]}
    engine = GroupResult(name="Midterm", weight=25, kept=1, dropped=[], earned=31, possible=40, pct=77.5, contribution=None)
    out = ex._with_entries(comp, group, engine)
    assert out["graded"] == 1 and out["average_pct"] == 77.5
    assert out["items"][-1] == {"name": "Midterm (entered by you, from Gradescope)", "pct": 77.5, "status": "entered by you"}
    assert ex._with_entries({"items": [], "graded": 2}, None, None) == {"items": [], "graded": 2}
