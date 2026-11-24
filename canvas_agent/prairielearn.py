"""PrairieLearn connector: normalise the collector's output and merge it with Canvas. Pure Python, no LLM.

Input: data/prairielearn_snapshot.json, written from canvas_agent/connectors/prairielearn_collector.js run in the
student's signed-in PrairieLearn tab (GET only; group members are never collected).

What it gets right that a naive reader gets wrong:
- "0%" with questions "saved for manual grading" is SUBMITTED, awaiting a grade, not a zero.
- "Score not shown" may still have visible points (e.g. 24/24): use them, mark the score as hidden.
- Redo copies, practice sets and practice instances are NOT graded work by default.
- "100% until Thu, Nov 26, end of day" is a credit deadline (parsed in the course's time zone);
  "Available Sat, Dec 5, start of day" is a future opening; "None" means no credit is available any more.
- Canvas is never overwritten: PrairieLearn only fills Canvas items/groups that have no released score, and
  any disagreement between the two is reported as a conflict, not resolved.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from canvas_agent.ingest import course_code

Kind = Literal["graded", "redo", "practice", "practice_template", "tutorial"]
Status = Literal["graded", "score_hidden", "awaiting_grade", "partially_graded", "open_not_done",
                 "not_started", "not_yet_available", "closed_no_credit", "no_points"]


class PLAssessment(BaseModel):
    course: str
    set: str
    label: str
    title: str
    kind: Kind
    status: Status
    earned: float | None
    possible: float | None
    pct: float | None  # earned/possible, capped at 100 like PrairieLearn's own display
    pending_questions: int
    pending_points: float
    credit_pct: float | None
    deadline: datetime | None
    available_from: datetime | None
    open: bool


class MergeNote(BaseModel):
    canvas_item: str | None
    canvas_group: str
    pl_assessment: str
    action: Literal["filled", "added", "same", "conflict"]
    detail: str


_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_PTS = re.compile(r"(pending|—|[\d.]+)\s*/\s*([\d.]+)")


def _kind(set_name: str, label: str, title: str, score_text: str) -> Kind:
    s, t = (set_name or "").lower(), title.lower()
    if score_text == "New instance":
        return "practice_template"
    if "redo" in t:
        return "redo"
    if "practice" in s or label.startswith(("P-", "PRAQ")):
        return "practice"
    if "tutorial" in s:
        return "tutorial"
    return "graded"


def _when(text: str, collected: datetime, tz: ZoneInfo) -> datetime | None:
    """'today, 11:59pm' | 'Thu, Nov 26, end of day' | 'Fri, Nov 27, 6:05pm' | 'Sat, Dec 5, start of day'."""
    local = collected.astimezone(tz)
    t = text.strip().lower()
    if t.startswith("today"):
        day = local.date()
    elif t.startswith("tomorrow"):
        day = local.date() + timedelta(days=1)
    else:
        m = re.search(r"([a-z]{3})[a-z]*\s+(\d{1,2})", t.split(",", 1)[1] if "," in t else t)
        if not m or m.group(1) not in _MONTHS:
            return None
        month, dom = _MONTHS[m.group(1)], int(m.group(2))
        year = local.year + (1 if month < local.month - 6 else 0)  # a January date seen in November is next year
        day = local.date().replace(year=year, month=month, day=dom)
    if "end of day" in t:
        clock = time(23, 59, 59)
    elif "start of day" in t:
        clock = time(0, 0)
    else:
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", t)
        if not m:
            return None
        h = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        clock = time(h, int(m.group(2) or 0))
    return datetime.combine(day, clock, tzinfo=tz)


def parse_credit(text: str, collected: datetime, tz: ZoneInfo) -> tuple[float | None, datetime | None, datetime | None]:
    """-> (credit %, credit deadline, available-from)."""
    t = (text or "").strip()
    if not t:
        return None, None, None
    if t.lower() == "none":
        return 0.0, None, None
    if t.lower().startswith("available"):
        return None, None, _when(t[len("available"):], collected, tz)
    m = re.match(r"(\d+(?:\.\d+)?)%\s*(?:until\s+(.*))?$", t, re.I)
    if not m:
        return None, None, None
    return float(m.group(1)), (_when(m.group(2), collected, tz) if m.group(2) else None), None


def normalise(snapshot: dict) -> list[PLAssessment]:
    collected = datetime.fromisoformat(snapshot["collected_at"].replace("Z", "+00:00"))
    tz = ZoneInfo(snapshot.get("timezone", "America/Vancouver"))
    out: list[PLAssessment] = []
    for c in snapshot["courses"]:
        code = course_code({"id": c["course_instance_id"], "name": c["title"], "course_code": c["title"]})
        for a in c["assessments"]:
            kind = _kind(a.get("set"), a["label"], a["title"], a.get("score_text", ""))
            credit, deadline, avail = parse_credit(a.get("credit_text", ""), collected, tz)
            pts = a.get("points")
            earned, possible = (float(pts[0]), float(pts[1])) if pts else (None, None)
            pending_q, pending_pts, answered = 0, 0.0, 0
            for _, cells in a.get("questions", []):
                low = cells.lower()
                for got, of in _PTS.findall(cells):
                    if got == "pending":
                        pending_pts += float(of)
                if "pending" in low or "saved for manual grading" in low:
                    pending_q += 1
                if re.search(r"\b\d+(?:\.\d+)?\s*/", cells) or "complete" in low:
                    answered += 1
            score = a.get("score_text", "")
            if kind == "practice_template":
                status = "no_points"
            elif avail is not None:
                status = "not_yet_available"
            elif score == "Not started":
                status = "not_started"
            elif possible == 0:
                status = "no_points"  # participation-style item with no points
            elif pending_q and earned:
                status = "partially_graded"
            elif pending_q:
                status = "awaiting_grade"
            elif score == "Score not shown":
                status = "score_hidden"
            elif not answered and not earned:
                status = "open_not_done" if a.get("open") else "closed_no_credit"
            else:
                status = "graded"
            pct = round(min(100.0, 100 * earned / possible), 2) if (earned is not None and possible) else None
            out.append(PLAssessment(
                course=code, set=a.get("set") or "", label=a["label"], title=a["title"], kind=kind, status=status,
                earned=earned, possible=possible, pct=pct, pending_questions=pending_q, pending_points=pending_pts,
                credit_pct=credit, deadline=deadline, available_from=avail, open=bool(a.get("open")),
            ))
    return out


# ---- merge with Canvas --------------------------------------------------------------------------------------
def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+\d*|\d+", s.lower()))


def _label_keys(label: str) -> set[str]:
    k = label.lower()
    keys = {k}
    m = re.match(r"deliv(\d+)$", k)
    if m:
        keys.add(f"d{m.group(1)}")  # Canvas "D3-Total" <-> PrairieLearn "DELIV3"
    return keys


def merge_into_canvas(course: dict, pl: list[PLAssessment]) -> tuple[dict, list[MergeNote]]:
    """Fill Canvas gaps with PrairieLearn scores. Returns a new course dict + a note for every decision."""
    merged = copy.deepcopy(course)
    notes: list[MergeNote] = []
    graded = [a for a in pl if a.kind == "graded" and a.status in ("graded", "score_hidden", "partially_graded")
              and a.earned is not None and a.possible]
    used: set[str] = set()
    for g in merged["groups"]:
        for it in g["items"]:
            name = it[0]
            ntoks = _tokens(name.replace("_", " ").replace("-", " "))
            match = next((a for a in graded if a.label not in used and _label_keys(a.label) & ntoks
                          and ("total" in ntoks or not re.match(r"d\d", next(iter(_label_keys(a.label) & ntoks))))), None)
            if not match:
                continue
            used.add(match.label)
            pl_score = round(match.earned / match.possible * (it[2] or match.possible), 4) if match.status != "partially_graded" else None
            if it[4] is None and pl_score is not None:
                it[3], it[4] = "graded", pl_score
                notes.append(MergeNote(canvas_item=name, canvas_group=g["name"], pl_assessment=match.label, action="filled",
                                       detail=f"{match.earned:g}/{match.possible:g} on PrairieLearn -> {pl_score:g}/{it[2]:g} in Canvas terms"))
            elif it[4] is not None and pl_score is not None and abs(it[4] - pl_score) > 1e-6:
                notes.append(MergeNote(canvas_item=name, canvas_group=g["name"], pl_assessment=match.label, action="conflict",
                                       detail=f"Canvas {it[4]:g}/{it[2]:g} vs PrairieLearn {match.earned:g}/{match.possible:g}; Canvas kept"))
            elif it[4] is not None:
                notes.append(MergeNote(canvas_item=name, canvas_group=g["name"], pl_assessment=match.label, action="same",
                                       detail="Canvas and PrairieLearn agree"))
    # Weighted Canvas groups with no items (e.g. "ML 201 Midterm 1"): add the PrairieLearn assessment whose
    # title words (digits included) all appear in the group name.
    for g in merged["groups"]:
        if g["items"] or not g.get("weight"):
            continue
        gt = _tokens(g["name"])
        cands = [a for a in graded if a.label not in used and _tokens(a.title) <= gt]
        if len(cands) == 1:
            a = cands[0]
            used.add(a.label)
            g["items"].append([a.title, None, a.possible, "graded", a.earned, False])
            notes.append(MergeNote(canvas_item=None, canvas_group=g["name"], pl_assessment=a.label, action="added",
                                   detail=f"{a.earned:g}/{a.possible:g} from PrairieLearn; Canvas had no item"))
    merged.setdefault("sources", []).append("PrairieLearn")
    if any(n.action in ("filled", "added") for n in notes) and merged.get("canvas_totals"):
        # Canvas's own total only covers Canvas data, so it is no longer comparable once PrairieLearn fills gaps.
        merged["canvas_totals_canvas_only"] = merged.pop("canvas_totals")
    return merged, notes


def summarise(pl: list[PLAssessment], code: str, now: datetime) -> dict:
    """Per-set view for the dashboard and agents: graded averages (graded work only), pending, deadlines."""
    mine = [a for a in pl if a.course == code]
    sets: dict[str, dict] = {}
    for a in mine:
        s = sets.setdefault(a.set, {"set": a.set, "graded": 0, "earned": 0.0, "possible": 0.0, "awaiting": 0, "items": 0})
        s["items"] += 1
        if a.kind == "graded" and a.status in ("graded", "score_hidden") and a.possible:
            s["graded"] += 1
            s["earned"] += a.earned or 0
            s["possible"] += a.possible
        if a.status in ("awaiting_grade", "partially_graded"):
            s["awaiting"] += 1
    for s in sets.values():
        s["average_pct"] = round(100 * s["earned"] / s["possible"], 2) if s["possible"] else None
    due = sorted((a for a in mine if a.deadline and a.deadline >= now and a.kind in ("graded", "tutorial")),
                 key=lambda a: a.deadline)
    return {
        "course": code,
        "sets": list(sets.values()),
        "awaiting_grade": [{"label": a.label, "title": a.title, "pending_points": a.pending_points}
                           for a in mine if a.status in ("awaiting_grade", "partially_graded")],
        "credit_deadlines": [{"label": a.label, "title": a.title, "deadline": a.deadline.isoformat(),
                              "credit_pct": a.credit_pct, "status": a.status} for a in due],
        "not_yet_available": [{"label": a.label, "title": a.title, "opens": a.available_from.isoformat()}
                              for a in mine if a.available_from],
        "missed_or_zero": [{"label": a.label, "title": a.title, "status": a.status} for a in mine
                           if a.kind == "graded" and a.status in ("closed_no_credit", "open_not_done") and not a.deadline],
    }


def pl_course(pl: list[PLAssessment], code: str, name: str = "", canvas: dict | None = None) -> dict | None:
    """A points-only course built from PrairieLearn graded work, grouped by PrairieLearn set. Item names carry the
    set name ("Lab Assignments: LAB01 Onboarding") so syllabus components can be matched by meaning.
    Redo copies, practice and point-less tutorials are left out (their effect on the grade is a syllabus rule)."""
    released: dict[str, float] = {}  # Canvas released scores by label key, as a fraction (e.g. LAB06 -> 0.0)
    for g in (canvas or {}).get("groups", []):
        for it in g["items"]:
            if it[4] is not None and it[2]:
                for k in _tokens(it[0].replace("_", " ").replace("-", " ")):
                    released.setdefault(k, it[4] / it[2])
                released.setdefault(it[0].lower(), it[4] / it[2])
    groups: dict[str, list] = {}
    for a in pl:
        if a.course != code or a.kind != "graded" or not a.possible:
            continue
        state = {"graded": "graded", "score_hidden": "graded", "partially_graded": "pending_review",
                 "awaiting_grade": "pending_review"}.get(a.status, "unsubmitted")
        score = a.earned if a.status in ("graded", "score_hidden") else None
        if a.status == "closed_no_credit":  # window closed, nothing earned: a real zero
            state, score = "graded", 0.0
        if score is None and state == "unsubmitted" and not a.deadline:
            # no PrairieLearn score and no open credit window: use Canvas's released score for the same item if any
            frac = next((released[k] for k in _label_keys(a.label) | {f"{a.label.lower()}-total"} if k in released), None)
            if frac is not None:
                state, score = "graded", round(frac * a.possible, 4)
        due = a.deadline.isoformat() if a.deadline else None
        groups.setdefault(a.set, []).append([f"{a.set}: {a.label} {a.title}", due, a.possible, state, score, False])
    if not groups:
        return None
    return {"name": name, "weighted": False, "omit_from_final": [], "sources": ["PrairieLearn"],
            "groups": [{"name": k, "weight": 0, "rules": {}, "items": v} for k, v in groups.items()]}
