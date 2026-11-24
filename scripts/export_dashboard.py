"""Export every course into the dashboard JSON contract used by the Lovable UI (src/data/dashboard.json there).
Free, deterministic, local only: writes data/dashboard.json (gitignored), never uploads anything.

    .venv/bin/python -m scripts.export_dashboard

The field names match the contract in the UI brief; every number comes from the Python engine.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from canvas_agent import course_view as cv
from canvas_agent.grades import grade_summary
from canvas_agent.tools import Store

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _component(c: dict) -> dict:
    """One component in UI form; tolerant of partial records (missing fields become empty / null)."""
    items = []
    for i in c.get("items", []):
        status = ("dropped" if i.get("dropped_syllabus") else "counted") if i.get("score") is not None else "not graded yet"
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
        warnings = list((facts or {}).get("past_exams_without_score", []))
        warnings += [w for w in canvas.warnings if not (facts and "syllabus weights are needed" in w)]
        att = (facts or {}).get("attendance_check")
        if att and att.get("exceeds"):
            warnings.insert(0, f"Attendance: {len(att['possible_absences'])} of {att['sessions_total']} classes missed "
                               f"({att['possible_absence_pct']}%), above the syllabus limit(s) {att['exceeds']}%.")
        courses.append({
            "code": code, "name": course["name"],
            "grade": p["grade"],
            "progress": {"course_progress_pct": p["course_progress_pct"], "label": label},
            "needed": (facts or {}).get("needed") or {},
            "pass_requirements": [{k: pr[k] for k in ("components", "threshold_pct", "current_pct", "needed_on_remaining_pct", "status")}
                                  for pr in (facts or {}).get("pass_requirements", [])],
            "warnings": warnings,
            "input_requests": [{k: r.get(k) for k in ("component", "weight_pct", "reason", "likely_source")}
                               for r in (facts or {}).get("input_requests", [])],
            "components": [_component(c) for c in (facts or {}).get("component_checks", [])],
            "notes": [{"order": i + 1, "label": (f"Week {s['week']}" if s["week"] else s["kind"].title()), "title": s["title"],
                       "goals": s["goals"], "points": s["points"][:20], "scores": s["scores"],
                       "source_url": s["source"] if s["source"].startswith("http") else None}
                      for i, s in enumerate(cv.notes(store, code, now))],
            "plan": {k: v for k, v in cv.plan(store, code, now).items() if k in ("weak_areas", "days")},
            "deadlines": [{"when": d["when"].isoformat(), "what": d["what"], "pct_of_grade": d["pct_of_grade"],
                           "status": d["status"], "sources": d["sources"], "days_left": d["days_left"]}
                          for d in cv.deadlines(store, code, now)],
            "sources": _sources(store, code, facts),
        })
    return {"generated_at": now.isoformat(), "courses": courses, "ask_examples": []}


def main() -> None:
    out = export()
    path = DATA / "dashboard.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote {path} ({len(out['courses'])} courses, {path.stat().st_size // 1024} KB) - local only, not uploaded")


if __name__ == "__main__":
    main()
