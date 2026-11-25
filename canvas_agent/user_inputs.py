"""What the student tells us when no connector can reach the data (Gradescope, iClicker, paper exams).

Two things are asked for every gap:
  1. the grade: score / out of, and where it came from (e.g. Gradescope)
  2. scope for improvement: what marks were lost on / what to work on (free text, split into areas)

Grades enter the syllabus-rules grade as items marked "(entered by you)" so they are never mistaken for
system data. Improvement areas become a study-focus list matched to course content by search (no LLM).
Stored locally in data/user_inputs.json (gitignored).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class InputRequest(BaseModel):
    course: str
    component: str
    weight_pct: float
    reason: str
    likely_source: str | None = None
    asks: list[str] = ["score", "out_of", "improvement_areas"]


class UserEntry(BaseModel):
    course: str
    component: str
    item: str = Field(description="Name of the assessment, e.g. 'Midterm'.")
    score: float | None = None
    out_of: float | None = None
    source: str | None = None  # e.g. Gradescope
    improvement_areas: list[str] = []
    notes: str = ""
    entered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("out_of")
    @classmethod
    def _positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("out_of must be positive")
        return v

    @field_validator("score")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("score cannot be negative")
        return v


def split_areas(text: str) -> list[str]:
    """'lost marks on LSP; test doubles\n- REST design' -> ['lost marks on LSP', 'test doubles', 'REST design']"""
    parts = re.split(r"[;\n]|,(?![^()]*\))|\s+-\s+|^\s*-\s*", text or "", flags=re.M)
    return [p.strip(" -•*.") for p in parts if p and p.strip(" -•*.")]


class InputStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def all(self) -> list[UserEntry]:
        if not self.path.exists():
            return []
        return [UserEntry.model_validate(e) for e in json.loads(self.path.read_text()).get("entries", [])]

    def for_course(self, code: str) -> list[UserEntry]:
        return [e for e in self.all() if e.course == code]

    def add(self, entry: UserEntry) -> None:
        """Adding the same course + component + item again replaces the earlier entry (corrections)."""
        if entry.score is not None and entry.out_of is not None and entry.score > entry.out_of * 1.5:
            raise ValueError(f"score {entry.score} looks too high for out of {entry.out_of}")
        keep = [e for e in self.all() if (e.course, e.component, e.item) != (entry.course, entry.component, entry.item)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"_note": "Entered by the student. Local only.",
                                         "entries": [e.model_dump() for e in keep + [entry]]}, indent=1))


def input_requests(code: str, facts: dict, entries: list[UserEntry], tools: list[str] | None = None) -> list[InputRequest]:
    """Gaps worth asking about, most important first: components that should have a grade by now but have none
    in any connected system, then weighted components with no data source at all."""
    have = {e.component for e in entries if e.score is not None}
    reqs: list[InputRequest] = []
    likely = ", ".join(t for t in (tools or []) if t.lower() in ("gradescope", "iclicker")) or None
    past = facts.get("past_exams_without_score", [])
    for c in facts.get("component_checks", []):
        if c["component"] in have:
            continue
        overdue = next((p for p in past if p.startswith(c["component"])), None)
        if overdue:
            reqs.append(InputRequest(course=code, component=c["component"], weight_pct=c["syllabus_weight"],
                                     reason=overdue.split(" but ")[0] + ", but no system shows a score.",
                                     likely_source=likely))
        elif c["items_in_canvas"] == 0 and c["scored"] == 0 and not re.search(r"final", c["component"], re.I):
            reqs.append(InputRequest(course=code, component=c["component"], weight_pct=c["syllabus_weight"],
                                     reason="No connected system (Canvas, PrairieLearn) holds marks for this component.",
                                     likely_source="iClicker / instructor records" if re.search(r"particip|clicker|attend", c["component"], re.I) else likely))
    return sorted(reqs, key=lambda r: (0 if "scheduled" in r.reason else 1, -r.weight_pct))


def apply_entries(view: dict, entries: list[UserEntry]) -> tuple[dict, list[str]]:
    """Add the student's grades to a syllabus-view course (weighted groups named after components)."""
    import copy

    out = copy.deepcopy(view)
    notes = []
    groups = {g["name"]: g for g in out["groups"]}
    for e in entries:
        if e.score is None or e.out_of is None:
            continue
        g = groups.get(e.component)
        if g is None:
            notes.append(f"Skipped '{e.item}': no component named '{e.component}'.")
            continue
        label = f"{e.item} (entered by you{', from ' + e.source if e.source else ''})"
        if any(it[0] == label for it in g["items"]):
            continue
        # replace one unscored placeholder for this component if there is one, else add
        slot = next((it for it in g["items"] if it[4] is None and "(not yet in Canvas)" in it[0]), None)
        row = [label, None, float(e.out_of), "graded", float(e.score), False]
        if slot:
            g["items"][g["items"].index(slot)] = row
        else:
            g["items"].append(row)
        notes.append(f"{e.component}: {e.score:g}/{e.out_of:g} entered by you" + (f" (from {e.source})" if e.source else ""))
    return out, notes


ENTERED = "(entered by you"


def entries_into_checks(checks: list[dict], view: dict, code: str) -> list[dict]:
    """Show the student's entered marks in the per-component checks.

    Component checks are built from Canvas before `apply_entries` adds the student's own marks to the syllabus view,
    so without this an entered midterm reads "0 graded, no average" while the course grade already counts it. Adds
    each entered item (labelled "entered by you") and takes the component's count and average from the grade engine
    run on the same view, so the table and the grade can never disagree. Idempotent; returns new dicts."""
    import copy

    from canvas_agent.grades import grade_summary

    groups = {g["name"]: g for g in view.get("groups", [])}
    engine = {g.name: g for g in grade_summary(code, view).groups}
    out = copy.deepcopy(checks)
    for c in out:
        g, eg = groups.get(c["component"]), engine.get(c["component"])
        rows = [r for r in (g or {}).get("items", []) if ENTERED in r[0] and r[4] is not None and r[2]]
        have = {i["name"] for i in c["items"]}
        new = [r for r in rows if r[0] not in have]
        if not new:
            continue
        dropped = set(eg.dropped) if eg else set()
        c["items"] += [{"name": r[0], "points": r[2], "score": r[4], "pct": round(r[4] / r[2] * 100, 2),
                        "dropped_syllabus": r[0] in dropped, "dropped_canvas": False} for r in new]
        c["scored"] = (c.get("scored") or 0) + len(new)
        if eg is not None and eg.pct is not None:
            c["avg_syllabus_rules"] = round(eg.pct, 2)
    return out


_FILLER = set("""understanding understand code changes change different types type kinds kind its and in of the
how to with on for about what why when using use lost marks mark more better get got some various""".split())


def focus_query(area: str) -> str:
    """Keep the distinctive words of an improvement area: 'recursion and its different types' -> 'recursion'."""
    words = re.findall(r"[A-Za-z][\w-]*", area)
    keep = [w for w in words if w.lower() not in _FILLER]
    return " ".join(keep) or area


def study_focus(entries: list[UserEntry], site_dir: Path | None, k: int = 3) -> list[dict]:
    """Each improvement area the student named, with the course pages that cover it (BM25 search, no LLM)."""
    from canvas_agent.website import search

    out = []
    for e in entries:
        for area in e.improvement_areas:
            raw = search(site_dir, focus_query(area), k=k * 4) if site_dir and (site_dir / "index.json").exists() else []
            seen, hits = set(), []
            for h in raw:  # one entry per page
                if (h["url"], h["page"]) not in seen:
                    seen.add((h["url"], h["page"]))
                    hits.append(h)
            picked = hits[:k]
            lecture = next((h for h in hits if h["kind"] == "lecture"), None)
            if lecture and lecture not in picked:  # always include the best slide: that is what students revise from
                picked = picked[: k - 1] + [lecture] if k > 1 else [lecture]
            out.append({"component": e.component, "item": e.item, "area": area,
                        "materials": [{"title": h["title"], "kind": h["kind"], "page": h["page"], "url": h["url"]} for h in picked]})
    return out
