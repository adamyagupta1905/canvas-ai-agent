"""Grade, tracker and coverage maths over a Canvas course snapshot. Pure Python, no LLM.

Snapshot shape (one course), as written by the Canvas pull into data/canvas_snapshot.json:
    {"name": str, "weighted": bool, "omit_from_final": [item names],
     "groups": [{"name": str, "weight": float, "rules": {"drop_lowest": n, "drop_highest": n},
                 "items": [[name, due_iso|None, points|None, state, score|None, late], ...]}]}

The engine reproduces Canvas's own gradebook rules, so the numbers can be checked against
Canvas wherever the instructor shows the course total:
- Items marked omit_from_final_grade never count.
- An item counts only if it has a released score. Unreleased/ungraded items are ignored for the
  "current" grade and scored as 0 for Canvas's "final" grade.
- Within a group: drop_lowest / drop_highest are applied first, choosing the drops that give the
  best group % (what Canvas does; for equal-point items this is simply the lowest scores).
  Group % = earned / possible over kept items. Points earned on 0-point items are extra credit.
- Weighted course: groups with no countable points are left out and the remaining weights are
  re-normalised. Unweighted course: total earned / total possible across all groups.
Not knowable from Canvas and therefore NOT applied: syllabus rules kept off Canvas, curves, scaling,
and marks recorded only in outside tools (PrairieLearn, Gradescope, iClicker before sync).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Callable

from pydantic import BaseModel

DONE_STATES = {"graded", "submitted", "pending_review"}
_BRUTE_FORCE_LIMIT = 200_000


class TrackerRow(BaseModel):
    course: str
    group: str
    name: str
    due: datetime | None
    points: float | None
    score: float | None
    pct: float | None
    status: str  # graded | awaiting_grade | upcoming | past_due_unsubmitted | no_due_date | omitted
    weight_of_final: float | None  # % of the final course grade this item is worth (before drops)
    size: str  # major | minor | unknown
    late: bool


class GroupResult(BaseModel):
    name: str
    weight: float
    kept: int
    dropped: list[str]
    earned: float
    possible: float
    pct: float | None  # None = group has no countable points, so it is left out
    contribution: float | None  # weighted: points of the final grade this group currently adds


class GradeResult(BaseModel):
    pct: float | None
    weight_counted: float | None  # weighted courses: sum of weights of groups that counted
    groups: list[GroupResult]


class GradeSummary(BaseModel):
    course: str
    weighted: bool
    current_pct: float | None  # Canvas "current": only released scores count
    canvas_final_pct: float | None  # Canvas "final": unreleased/ungraded scored 0, empty groups left out
    floor_pct: float | None  # worst case: every remaining item and empty group scores 0
    ceiling_pct: float | None  # best case: everything remaining scores 100%
    weight_determined: float | None
    groups: list[GroupResult]
    canvas_check: dict | None  # our numbers vs Canvas's own totals, where Canvas shows them
    warnings: list[str]


def _parse(due: str | None) -> datetime | None:
    return datetime.fromisoformat(due.replace("Z", "+00:00")) if due else None


def _items(group: dict):
    for name, due, pts, state, score, late in group["items"]:
        yield name, _parse(due), pts, state, score, late


def _pct(pairs) -> float:
    earned = sum(s for s, _, _ in pairs)
    possible = sum(p for _, p, _ in pairs)
    return earned / possible if possible else float("-inf")


def _item_keys(name: str) -> set[str]:
    """'pct_hw5' / 'Homework 5' / 'HW05' -> {'hw5'}; 'Lab 3' -> {'lab3'} (used for never-drop matching)."""
    n = name.lower().replace("_", " ")
    return {f"{ {'homework': 'hw', 'assignment': 'a', 'quiz': 'quiz', 'lab': 'lab', 'hw': 'hw'}[p] }{int(d)}".replace(" ", "")
            for p, d in re.findall(r"\b(homework|hw|assignment|quiz|lab)\s*0*(\d+)", n)}


def _apply_drops(pairs: list[tuple[float, float, str]], rules: dict) -> tuple[list, list[str]]:
    """pairs: (score, points, name). Returns (kept, dropped names), choosing drops that maximise the group %."""
    lo, hi = int(rules.get("drop_lowest") or 0), int(rules.get("drop_highest") or 0)
    if rules.get("never_drop"):
        raise NotImplementedError("Canvas never_drop (assignment ids) is not supported; use never_drop_keys")
    protect = {k.lower() for k in rules.get("never_drop_keys", [])}
    if protect:  # e.g. 'Homework 5 cannot be dropped': keep those out of the drop choice entirely
        keep = [p for p in pairs if _item_keys(p[2]) & protect]
        rest = [p for p in pairs if not (_item_keys(p[2]) & protect)]
        kept, dropped = _apply_drops(rest, {k: v for k, v in rules.items() if k != "never_drop_keys"})
        return keep + kept, dropped
    n = len(pairs)
    lo = min(lo, max(n - 1, 0))
    hi = min(hi, max(n - 1 - lo, 0))
    if not lo and not hi:
        return pairs, []
    idx = range(n)
    kept_idx = list(idx)
    if lo:
        cands = list(combinations(idx, lo))
        if len(cands) <= _BRUTE_FORCE_LIMIT:
            best = max(cands, key=lambda drop: _pct([pairs[i] for i in idx if i not in drop]))
        else:  # greedy fallback for very large groups
            best = tuple(sorted(idx, key=lambda i: pairs[i][0] / pairs[i][1] if pairs[i][1] else 1)[:lo])
        kept_idx = [i for i in idx if i not in best]
    if hi:  # drop the highest: choose drops that leave the LOWEST remaining % among the best scores
        cands = list(combinations(kept_idx, hi))
        worst = min(cands, key=lambda drop: _pct([pairs[i] for i in kept_idx if i not in drop]))
        kept_idx = [i for i in kept_idx if i not in worst]
    kept = [pairs[i] for i in kept_idx]
    dropped = [pairs[i][2] for i in idx if i not in kept_idx]
    return kept, dropped


def compute_grade(course: dict, assume: float | None = None, fill_empty_groups: bool = False) -> GradeResult:
    """Core calculation.

    assume=None  -> only released scores count (Canvas "current").
    assume=a     -> every item without a released score is scored a * its points (a=0 is Canvas "final").
    fill_empty_groups -> weighted groups with no items yet (e.g. a final exam not created in Canvas)
                         count at `assume` too, so the projection covers 100% of the weight.
    """
    omit = set(course.get("omit_from_final", []))
    results: list[GroupResult] = []
    num = den = 0.0
    tot_e = tot_p = 0.0
    for g in course["groups"]:
        pairs = []
        for name, _, pts, _, score, _ in _items(g):
            if name in omit:
                continue
            if score is not None:
                pairs.append((float(score), float(pts or 0), name))
            elif assume is not None and pts:
                pairs.append((assume * pts, float(pts), name))
        kept, dropped = _apply_drops(pairs, g.get("rules") or {})
        earned = sum(s for s, _, _ in kept)
        possible = sum(p for _, p, _ in kept)
        pct = earned / possible if possible else None
        if pct is None and fill_empty_groups and assume is not None and not g["items"] and g["weight"]:
            pct, possible = assume, 0.0
        contribution = None
        if course["weighted"] and pct is not None and g["weight"]:
            num += g["weight"] * pct
            den += g["weight"]
            contribution = g["weight"] * pct
        tot_e += earned
        tot_p += possible
        results.append(GroupResult(
            name=g["name"], weight=g["weight"], kept=len(kept), dropped=dropped,
            earned=round(earned, 4), possible=round(possible, 4),
            pct=round(100 * pct, 4) if pct is not None else None,
            contribution=round(contribution, 4) if contribution is not None else None,
        ))
    if course["weighted"]:
        return GradeResult(pct=100 * num / den if den else None, weight_counted=den, groups=results)
    return GradeResult(pct=100 * tot_e / tot_p if tot_p else None, weight_counted=None, groups=results)


def _r(x: float | None, nd: int = 2) -> float | None:
    return round(x, nd) if x is not None else None


def grade_summary(code: str, course: dict) -> GradeSummary:
    warnings: list[str] = []
    current = compute_grade(course)
    final = compute_grade(course, assume=0.0)
    floor = compute_grade(course, assume=0.0, fill_empty_groups=True)
    ceiling = compute_grade(course, assume=1.0, fill_empty_groups=True)

    if course["weighted"]:
        wsum = sum(g["weight"] for g in course["groups"])
        if abs(wsum - 100) > 0.01:
            warnings.append(f"Group weights sum to {wsum}, not 100; Canvas re-normalises.")
        for g in course["groups"]:
            if g["weight"] and not g["items"]:
                warnings.append(f"'{g['name']}' ({g['weight']}%) has no items in Canvas yet; it is in the floor/ceiling but not the current grade.")
            elif g["weight"] and g["items"] and all(it[4] is None for it in g["items"]):
                warnings.append(f"'{g['name']}' ({g['weight']}%) has no released scores yet (not done, not marked, or marked in another tool).")
            scored = [it[4] for it in g["items"] if it[4] is not None and it[2]]
            if g["weight"] and len(scored) >= 5 and all(x == 0 for x in scored):
                warnings.append(f"Every released score in '{g['name']}' ({g['weight']}%) is 0 across {len(scored)} items. "
                                "Check whether these marks are synced (e.g. iClicker/attendance) before trusting the grade.")
    else:
        warnings.append("Canvas has no group weights here: this is a points total. The syllabus weights are needed for the real course grade.")

    check = None
    if course.get("canvas_totals"):
        ct = course["canvas_totals"]
        check = {
            "canvas_current": ct["current"], "ours_current": _r(current.pct),
            "canvas_final": ct["final"], "ours_final": _r(final.pct),
            "match": _r(current.pct) == ct["current"] and _r(final.pct) == ct["final"],
        }
        if not check["match"]:
            warnings.append("Our numbers do not match Canvas's own totals; investigate before trusting them.")

    return GradeSummary(
        course=code, weighted=course["weighted"],
        current_pct=_r(current.pct), canvas_final_pct=_r(final.pct),
        floor_pct=_r(floor.pct), ceiling_pct=_r(ceiling.pct),
        weight_determined=_r(current.weight_counted), groups=current.groups,
        canvas_check=check, warnings=warnings,
    )


def needed_on_remaining(course: dict, target_pct: float) -> float | None:
    """Uniform % needed on every remaining item (and empty weighted group) to finish at target_pct.

    Found by bisection on the full projection, so drop rules and group weights are respected.
    A value above 100 means the target is out of reach (it shows by how much).
    Returns None only when nothing is left to score.
    """
    lo_g = compute_grade(course, assume=0.0, fill_empty_groups=True).pct
    hi_g = compute_grade(course, assume=1.0, fill_empty_groups=True).pct
    if lo_g is None or hi_g is None or abs(hi_g - lo_g) < 1e-9:
        return None
    if target_pct <= lo_g:
        return 0.0
    if target_pct > hi_g:
        # linear extrapolation beyond 100% tells you how far out of reach the target is
        return round(100 * (target_pct - lo_g) / (hi_g - lo_g), 2)
    a, b = 0.0, 1.0
    for _ in range(60):
        m = (a + b) / 2
        if compute_grade(course, assume=m, fill_empty_groups=True).pct < target_pct:
            a = m
        else:
            b = m
    return round(100 * b, 2)


def tracker(code: str, course: dict, now: datetime, major_threshold: float = 5.0) -> list[TrackerRow]:
    """One row per assignment, from tiny participation marks to the final exam."""
    rows: list[TrackerRow] = []
    omit = set(course.get("omit_from_final", []))
    course_pts = sum((pts or 0) for g in course["groups"] for n, _, pts, *_ in _items(g) if n not in omit)
    for g in course["groups"]:
        group_pts = sum((pts or 0) for n, _, pts, *_ in _items(g) if n not in omit)
        for name, due, pts, state, score, late in _items(g):
            if name in omit:
                rows.append(TrackerRow(course=code, group=g["name"], name=name, due=due, points=pts,
                                       score=score, pct=None, status="omitted", weight_of_final=0.0,
                                       size="minor", late=bool(late)))
                continue
            if course["weighted"]:
                w = g["weight"] * pts / group_pts if (pts and group_pts) else (0.0 if g["weight"] == 0 else None)
            else:
                w = None  # unknown without syllabus weights; share of Canvas points is not the grade
            if state == "graded" and score is not None:
                status = "graded"
            elif state in DONE_STATES:
                status = "awaiting_grade"
            elif due is None:
                status = "no_due_date"
            elif due < now:
                status = "past_due_unsubmitted"
            else:
                status = "upcoming"
            if w is None:
                # No weights: fall back to share of Canvas points (a rough proxy, labelled as such).
                if not course_pts:
                    size = "unknown"
                else:
                    size = "major" if (pts or 0) / course_pts * 100 >= major_threshold else "minor"
            else:
                size = "major" if w >= major_threshold else "minor"
            rows.append(TrackerRow(
                course=code, group=g["name"], name=name, due=due, points=pts, score=score,
                pct=round(100 * score / pts, 1) if (score is not None and pts) else None,
                status=status, weight_of_final=round(w, 3) if w is not None else None,
                size=size, late=bool(late),
            ))
    return rows


def term_progress(rows: list[TrackerRow], now: datetime) -> float | None:
    """% of dated assessments whose due date has passed: a proxy for how far through the course you are."""
    dated = [r for r in rows if r.due is not None]
    if not dated:
        return None
    return round(100 * sum(r.due <= now for r in dated) / len(dated), 1)


def topic_mastery(rows: list[TrackerRow], topic_of: Callable[[str], str | None]) -> dict[str, float]:
    """Score % per topic from graded evidence. topic_of maps an item name to a topic key (or None to skip)."""
    earned: dict[str, float] = defaultdict(float)
    possible: dict[str, float] = defaultdict(float)
    for r in rows:
        t = topic_of(r.name)
        if t and r.score is not None and r.points:
            earned[t] += r.score
            possible[t] += r.points
    return {t: round(100 * earned[t] / possible[t], 1) for t in possible}


def prefix_topic(pattern: str) -> Callable[[str], str | None]:
    """Build a topic_of function from a regex whose first group is the topic key."""
    rx = re.compile(pattern)

    def topic_of(name: str) -> str | None:
        m = rx.match(name)
        return m.group(1) if m else None

    return topic_of


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
