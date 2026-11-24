"""Deterministic tools the agents call. Every number an agent reports comes from one of these.

Each tool reads the local snapshot (data/canvas_snapshot.json) and syllabus results
(data/syllabi/<COURSE>.facts.json) and returns plain JSON-able data. No LLM in here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from canvas_agent.grades import grade_summary, needed_on_remaining, tracker

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, str]  # name -> "type: meaning"
    fn: Callable[..., object]


class Store:
    """Where the tools read from. Tests point it at a temp folder."""

    def __init__(self, root: Path = DATA):
        self.root = Path(root)

    def canvas_courses(self) -> dict:
        p = self.root / "canvas_snapshot.json"
        return json.loads(p.read_text())["courses"] if p.exists() else {}

    def pl(self) -> list:
        """Normalised PrairieLearn assessments (empty if no PrairieLearn snapshot)."""
        from canvas_agent.prairielearn import normalise

        p = self.root / "prairielearn_snapshot.json"
        return normalise(json.loads(p.read_text())) if p.exists() else []

    def courses(self) -> dict:
        """Canvas courses with PrairieLearn scores merged in where Canvas has gaps (Canvas is never overwritten)."""
        from canvas_agent.prairielearn import merge_into_canvas

        courses, pl = self.canvas_courses(), self.pl()
        if not pl:
            return courses
        out = {}
        for code, c in courses.items():
            mine = [a for a in pl if a.course == code]
            out[code] = merge_into_canvas(c, mine)[0] if mine else c
        return out

    def merge_notes(self, code: str) -> list:
        from canvas_agent.prairielearn import merge_into_canvas

        c, mine = self.canvas_courses().get(code), [a for a in self.pl() if a.course == code]
        return merge_into_canvas(c, mine)[1] if (c and mine) else []

    def facts(self, code: str) -> dict | None:
        p = self.root / "syllabi" / f"{code}.facts.json"
        return json.loads(p.read_text()) if p.exists() else None


def build_tools(store: Store, now: datetime | None = None) -> dict[str, Tool]:
    now = now or datetime.now(timezone.utc)

    def _course(code: str) -> dict:
        c = store.courses().get(code)
        if c is None:
            raise ValueError(f"Unknown course {code!r}. Known: {sorted(store.courses())}")
        return c

    def list_courses() -> list[dict]:
        return [{"code": k, "name": v["name"], "has_syllabus_rules": store.facts(k) is not None}
                for k, v in store.courses().items()]

    def course_grade(code: str) -> dict:
        f = store.facts(code)
        if f and f.get("syllabus_view_grade"):
            g = f["syllabus_view_grade"]
            return {"code": code, "basis": "syllabus weights and rules", "current_pct": g["current_pct"],
                    "weight_graded_pct": g["weight_determined"], "floor_pct": g["floor_pct"],
                    "ceiling_pct": g["ceiling_pct"], "canvas_calculation_pct": f["canvas_view_grade"]["current_pct"]}
        s = grade_summary(code, _course(code))
        return {"code": code, "basis": "Canvas setup only (no syllabus rules yet)", "current_pct": s.current_pct,
                "weight_graded_pct": s.weight_determined, "floor_pct": s.floor_pct, "ceiling_pct": s.ceiling_pct,
                "warnings": s.warnings}

    def needed_for_target(code: str, target_pct: float) -> dict:
        f = store.facts(code)
        view = f.get("syllabus_view_course") if f else None
        course = view or _course(code)
        n = needed_on_remaining(course, float(target_pct))
        return {"code": code, "target_pct": float(target_pct), "needed_avg_pct_on_remaining": n,
                "reachable": n is not None and n <= 100,
                "basis": "syllabus weights and rules" if view else "Canvas setup only"}

    def component_breakdown(code: str) -> list[dict]:
        f = store.facts(code)
        if f and f.get("component_checks"):
            return [{"component": c["component"], "weight_pct": c["syllabus_weight"], "average_pct": c["avg_syllabus_rules"],
                     "graded": c["scored"], "expected": c["expected_items"], "drop_lowest": c["drop_syllabus"],
                     "flags": c["flags"]} for c in f["component_checks"]]
        s = grade_summary(code, _course(code))
        return [{"component": g.name, "weight_pct": g.weight, "average_pct": g.pct, "graded": g.kept,
                 "dropped": g.dropped} for g in s.groups]

    def attendance_status(code: str) -> dict:
        f = store.facts(code)
        a = f.get("attendance_check") if f else None
        if not a:
            return {"code": code, "available": False, "reason": "no attendance rule or session list found"}
        return {"code": code, "available": True, "status": a["status"], "absences": len(a["possible_absences"]),
                "classes_total": a["sessions_total"], "absence_pct": a["possible_absence_pct"],
                "limits": a.get("allowed_absences"), "over_thresholds_pct": a["exceeds"],
                "classes_remaining": a.get("sessions_remaining"), "syllabus_quotes": a["quotes"][:2]}

    def upcoming(code: str, days: int = 14) -> list[dict]:
        end = now + timedelta(days=int(days))
        rows = [r for r in tracker(code, _course(code), now) if r.status == "upcoming" and r.due and r.due <= end]
        out = [{"item": r.name, "due": r.due.isoformat(), "pct_of_final_grade": r.weight_of_final, "size": r.size,
                "source": "Canvas"} for r in rows]
        for a in store.pl():  # PrairieLearn credit deadlines (where many course deadlines actually live)
            if a.course == code and a.deadline and now <= a.deadline <= end and a.kind in ("graded", "tutorial"):
                out.append({"item": f"{a.label}: {a.title}", "due": a.deadline.isoformat(), "pct_of_final_grade": None,
                            "size": "unknown", "source": "PrairieLearn", "status": a.status,
                            "credit_pct_until_deadline": a.credit_pct})
        return sorted(out, key=lambda r: r["due"])

    def course_schedule(code: str) -> dict:
        from canvas_agent.website import find_doc, schedule_rows

        site = store.root / "sites" / code
        doc = find_doc(site, "schedule")
        if not doc:
            return {"available": False, "reason": "no course website schedule crawled for this course"}
        return {"available": True, "url": doc.url, "rows": schedule_rows(doc)}

    def search_course_content(code: str, query: str, kind: str | None = None) -> list[dict]:
        from canvas_agent.website import search

        res = search(store.root / "sites" / code, query, k=5, kinds=[kind] if kind else None)
        return res or [{"note": "no matches, or no course website crawled for this course"}]

    def course_progress(code: str) -> dict:
        from canvas_agent import course_view

        _course(code)
        return course_view.progress(store, code, now)

    def course_notes(code: str, week: int | None = None) -> list[dict]:
        from canvas_agent import course_view

        _course(code)
        secs = course_view.notes(store, code, now)
        if week is not None:
            secs = [s for s in secs if s["week"] == int(week)]
        return [{k: s[k] for k in ("week", "title", "goals", "points", "scores", "source")} | {"points": s["points"][:15]}
                for s in secs] or [{"note": "no notes for that selection"}]

    def deadlines(code: str) -> list[dict]:
        from canvas_agent import course_view

        _course(code)
        return [{"when": d["when"].isoformat(), "what": d["what"], "pct_of_grade": d["pct_of_grade"],
                 "status": d["status"], "sources": d["sources"], "days_left": d["days_left"]}
                for d in course_view.deadlines(store, code, now)]

    def study_plan(code: str) -> dict:
        from canvas_agent import course_view

        _course(code)
        return course_view.plan(store, code, now)

    def input_requests(code: str) -> list[dict]:
        f = store.facts(code) or {}
        return f.get("input_requests", []) or [{"note": "nothing missing that the student needs to provide"}]

    def study_focus(code: str) -> list[dict]:
        from canvas_agent.user_inputs import InputStore, study_focus as _focus

        entries = InputStore(store.root / "user_inputs.json").for_course(code)
        return _focus(entries, store.root / "sites" / code) or [{"note": "no improvement areas entered yet"}]

    def prairielearn_status(code: str) -> dict:
        from canvas_agent.prairielearn import summarise

        s = summarise(store.pl(), code, now)
        s["merged_into_canvas"] = [n.model_dump() for n in store.merge_notes(code)]
        return s

    def syllabus_rules(code: str, component: str | None = None) -> list[dict]:
        f = store.facts(code)
        if not f:
            return [{"note": "no syllabus extraction for this course yet"}]
        rules = [r for r in f.get("rules", []) if not component or component.lower() in r["component"].lower()]
        return [{"component": r["component"], "kind": r["kind"], "rule": r["summary"], "quote": r["source_quote"]}
                for r in rules]

    def exam_dates(code: str) -> dict:
        f = store.facts(code) or {}
        return {"extracted": [{"exam": e["title"], "date": e["exam_date"], "wording": e["when_text"]}
                              for e in f.get("facts", {}).get("exams", [])],
                "found_by_python_unconfirmed": [{"exam": h["kind"], "date": h["found_date"], "quote": h["quote"]}
                                                for h in f.get("exam_date_hints", [])]}

    tools = [
        Tool("list_courses", "List the student's courses.", {}, list_courses),
        Tool("course_grade", "Current grade, graded weight, and floor/ceiling for a course.", {"code": "str: course code e.g. LAW201"}, course_grade),
        Tool("needed_for_target", "Average % needed on all remaining work to finish at a target grade.",
             {"code": "str: course code", "target_pct": "number: target final grade, e.g. 80"}, needed_for_target),
        Tool("component_breakdown", "Per-component weights, averages, counts, drops and flags.", {"code": "str: course code"}, component_breakdown),
        Tool("attendance_status", "Absences vs the syllabus attendance limits.", {"code": "str: course code"}, attendance_status),
        Tool("upcoming", "Items due in the next N days with their share of the final grade.",
             {"code": "str: course code", "days": "int: look-ahead window, default 14"}, upcoming),
        Tool("syllabus_rules", "Grading rules from the syllabus with exact quotes, optionally for one component.",
             {"code": "str: course code", "component": "str (optional): component name filter"}, syllabus_rules),
        Tool("prairielearn_status", "PrairieLearn per-set averages, work awaiting manual grading, credit deadlines, "
             "and which PrairieLearn scores were merged into the Canvas grade.", {"code": "str: course code"}, prairielearn_status),
        Tool("course_schedule", "Week-by-week schedule and deadline table from the course website.", {"code": "str: course code"}, course_schedule),
        Tool("search_course_content", "Search lecture slides, textbook, project specs and syllabus on the course website; "
             "returns snippets with their URL and page.", {"code": "str: course code", "query": "str: what to look for",
                                                            "kind": "str (optional): lecture | textbook | project | syllabus | schedule"},
             search_course_content),
        Tool("course_progress", "How far the course has got (weeks / classes / dated items) plus the grade summary.",
             {"code": "str: course code"}, course_progress),
        Tool("course_notes", "Structured notes in course order (titles, learning goals, topics covered, your scores, source).",
             {"code": "str: course code", "week": "int (optional): only this week"}, course_notes),
        Tool("deadlines", "Every upcoming deadline and exam merged from Canvas, PrairieLearn, the course website and the syllabus.",
             {"code": "str: course code"}, deadlines),
        Tool("study_plan", "Day-by-day plan: work blocks before deadlines, exam review on weak areas first.",
             {"code": "str: course code"}, study_plan),
        Tool("input_requests", "Grades the system cannot see (e.g. Gradescope, iClicker) that the student should provide; "
             "ask the student for the score and what they lost marks on.", {"code": "str: course code"}, input_requests),
        Tool("study_focus", "The student's own improvement areas with matching course pages to study.",
             {"code": "str: course code"}, study_focus),
        Tool("exam_dates", "Exam dates from the syllabus, plus dates Python found that need confirming.", {"code": "str: course code"}, exam_dates),
    ]
    return {t.name: t for t in tools}
