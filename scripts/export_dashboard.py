"""Export every course into the dashboard JSON contract read by the Orbit web UI (`web/`, served by
`scripts.serve_web`) and by any outside front end. Free, deterministic, local only: writes
`<COURSE_DATA_DIR>/dashboard.json` (default `data/`, gitignored), never uploads anything.

    .venv/bin/python -m scripts.export_dashboard

Every number, including the grade band and the 14-day timeline, comes from Python; the UI only draws it.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from canvas_agent import course_view as cv
from canvas_agent.grades import grade_summary
from canvas_agent.tools import Store
from canvas_agent.user_inputs import ENTERED, entries_into_checks

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("COURSE_DATA_DIR") or ROOT / "data")
BANDS = ((75.0, "safe"), (65.0, "watch"))  # current grade >= 75 safe, >= 65 watch, else at_risk
TIMELINE_DAYS = 14


def band(pct: float | None) -> str | None:
    """Grade band for colour coding; None when there is no grade yet (never guessed)."""
    if pct is None:
        return None
    return next((name for floor, name in BANDS if pct >= floor), "at_risk")


def data_dir_and_now(root: Path = ROOT) -> tuple[Path, datetime, bool]:
    """Same choice as the Streamlit app: COURSE_DATA_DIR, else data/, else the fictional demo; the demo has a frozen
    "today" in demo_meta.json. Returns (folder, now, is_demo)."""
    d = Path(os.environ.get("COURSE_DATA_DIR") or root / "data")
    if not (d / "canvas_snapshot.json").exists() and (root / "demo_data" / "canvas_snapshot.json").exists():
        d = root / "demo_data"
    meta = d / "demo_meta.json"
    if meta.exists():
        return d, datetime.fromisoformat(json.loads(meta.read_text())["as_of"]), True
    return d, datetime.now(timezone.utc), False


def _component(c: dict) -> dict:
    """One component in UI form; tolerant of partial records (missing fields become empty / null)."""
    items = []
    for i in c.get("items", []):
        status = ("not graded yet" if i.get("score") is None else "dropped" if i.get("dropped_syllabus") else
                  "entered by you" if ENTERED in i["name"] else "counted")
        items.append({"name": i["name"], "pct": i.get("pct"), "status": status})
    rules = []
    for r in c.get("rules", []):  # stored as 'kind: summary ("quote", chunk)'
        m = re.match(r'(\w+): (.*) \("(.*)", [^)]*\)$', r, re.S)
        rules.append({"kind": m.group(1), "summary": m.group(2), "quote": m.group(3)} if m else
                     {"kind": "other", "summary": r, "quote": ""})
    expected = c.get("expected_items") if c.get("expected_items") is not None else c.get("items_in_canvas")
    return {"component": c["component"], "weight_pct": c.get("syllabus_weight"), "graded": c.get("scored"),
            "expected": expected, "drop_lowest": c.get("drop_syllabus"), "average_pct": c.get("avg_syllabus_rules"),
            "items": items, "rules": rules}


def _sources(store: Store, code: str, facts: dict | None) -> list[dict]:
    out = []
    DATA = store.root
    snap = DATA / "canvas_snapshot.json"
    if snap.exists():
        out.append({"name": "Canvas", "last_synced": datetime.fromtimestamp(snap.stat().st_mtime, timezone.utc).isoformat(),
                    "detail": f"{sum(len(g['items']) for g in store.canvas_courses().get(code, {}).get('groups', []))} items"})
    pl = [a for a in store.pl() if a.course == code]
    if pl:
        out.append({"name": "PrairieLearn", "last_synced": json.loads((DATA / "prairielearn_snapshot.json").read_text())["collected_at"],
                    "detail": f"{len(pl)} assessments"})
    man = DATA / "sites" / code / "manifest.json"
    if man.exists():
        m = json.loads(man.read_text())
        out.append({"name": "Course website", "last_synced": m["crawled_at"], "detail": f"{len(m['docs'])} documents"})
    if facts:
        out.append({"name": "Syllabus", "last_synced": None, "detail": f"{len(facts.get('facts', {}).get('components', []))} components, "
                                                                       f"{len(facts.get('rules', []))} rules"})
    return out


def export(now: datetime | None = None, data_dir: Path = DATA) -> dict:
    now = now or datetime.now(timezone.utc)
    store = Store(data_dir)
    courses = []
    for code, course in store.courses().items():
        fp = data_dir / "syllabi" / f"{code}.facts.json"
        facts = json.loads(fp.read_text()) if fp.exists() else None
        p = cv.progress(store, code, now)
        label = (f"Week {p['weeks']['passed']} of {p['weeks']['total']}" if p.get("weeks") else
                 f"Class {p['sessions']['held']} of {p['sessions']['total']}" if p.get("sessions") and p["sessions"]["held"] else None)
        canvas = grade_summary(code, course)
        view = (facts or {}).get("syllabus_view_course")
        # an unweighted Canvas points total is not the course grade: no band, no average, no targets from it
        is_course_grade = p["grade"]["basis"] != "Canvas setup" or bool(course.get("weighted"))
        warnings = list((facts or {}).get("past_exams_without_score", []))
        warnings += [w for w in canvas.warnings if not (facts and "syllabus weights are needed" in w)]
        att = (facts or {}).get("attendance_check")
        if att and att.get("exceeds"):
            warnings.insert(0, f"Attendance: {len(att['possible_absences'])} of {att['sessions_total']} classes missed "
                               f"({att['possible_absence_pct']}%), above the syllabus limit(s) {att['exceeds']}%.")
        courses.append({
            "code": code, "name": course["name"],
            "grade": p["grade"], "grade_is_course_grade": is_course_grade,
            "band": band(p["grade"]["current_pct"]) if is_course_grade else None,
            "progress": {"course_progress_pct": p["course_progress_pct"], "label": label},
            "needed": (facts or {}).get("needed") or {},
            "pass_requirements": [{k: pr[k] for k in ("components", "threshold_pct", "current_pct", "needed_on_remaining_pct", "status")}
                                  for pr in (facts or {}).get("pass_requirements", [])],
            "warnings": warnings,
            "input_requests": [{k: r.get(k) for k in ("component", "weight_pct", "reason", "likely_source")}
                               for r in (facts or {}).get("input_requests", [])],
            # entries_into_checks is idempotent: also corrects facts files written before the fix
            "components": [_component(c) for c in (entries_into_checks(facts["component_checks"], view, code)
                                                   if view else (facts or {}).get("component_checks", []))],
            "notes": [{"order": i + 1, "label": (f"Week {s['week']}" if s["week"] else s["kind"].title()), "title": s["title"],
                       "goals": s["goals"], "points": s["points"][:20], "scores": s["scores"],
                       "source_url": s["source"] if s["source"].startswith("http") else None}
                      for i, s in enumerate(cv.notes(store, code, now))],
            "plan": {k: v for k, v in cv.plan(store, code, now).items() if k in ("weak_areas", "days")},
            "deadlines": [{"when": d["when"].isoformat(), "what": d["what"], "pct_of_grade": d["pct_of_grade"],
                           "status": d["status"], "status_label": d["status_label"], "sources": d["sources"],
                           "days_left": d["days_left"]}
                          for d in cv.deadlines(store, code, now)],
            "sources": _sources(store, code, facts),
        })
    grades = [c["grade"]["current_pct"] for c in courses
              if c["grade_is_course_grade"] and c["grade"]["current_pct"] is not None]
    timeline = sorted(({**d, "code": c["code"]} for c in courses for d in c["deadlines"]
                       if d["days_left"] is not None and 0 <= d["days_left"] <= TIMELINE_DAYS), key=lambda d: d["when"])
    summary = {"courses": len(courses), "graded_courses": len(grades),
               "average_current_pct": round(sum(grades) / len(grades), 2) if grades else None,
               "timeline_days": TIMELINE_DAYS, "bands": {"safe": BANDS[0][0], "watch": BANDS[1][0]}}
    return {"generated_at": now.isoformat(), "summary": summary, "timeline": timeline, "courses": courses,
            "ask_examples": []}


def main() -> None:
    data_dir, now, _ = data_dir_and_now()
    out = export(now, data_dir)
    path = data_dir / "dashboard.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote {path} ({len(out['courses'])} courses, {path.stat().st_size // 1024} KB) - local only, not uploaded")


if __name__ == "__main__":
    main()
