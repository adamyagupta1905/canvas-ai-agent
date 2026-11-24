"""Compare syllabus grade components with Canvas assignment groups. Pure Python.

Matching is by word overlap of the names, then the weights are compared. Nothing is merged or fixed:
every difference becomes a row the student (or the grade engine) can act on.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from canvas_agent.syllabus_flow import GradeComponent

_STOP = {"the", "and", "of", "a", "an", "in", "for", "on", "to", "exam", "exams", "s"}


class ReconRow(BaseModel):
    syllabus_component: str | None
    syllabus_weight: float | None
    canvas_group: str | None
    canvas_weight: float | None
    status: Literal["match", "weight_differs", "split_in_canvas", "merged_in_canvas", "only_in_syllabus",
                    "only_in_canvas", "canvas_unweighted"]
    similarity: float | None


def _words(name: str) -> set[str]:
    words = set(re.findall(r"[a-z]+", name.lower())) - _STOP
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words}


def similarity(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def reconcile(components: list[GradeComponent], course: dict, min_similarity: float = 0.34) -> list[ReconRow]:
    groups = [g for g in course["groups"] if g.get("weight") or not course["weighted"]]
    rows: list[ReconRow] = []
    if not course["weighted"]:
        for c in components:
            rows.append(ReconRow(syllabus_component=c.name, syllabus_weight=c.weight_pct, canvas_group=None,
                                 canvas_weight=None, status="canvas_unweighted", similarity=None))
        return rows

    pairs = sorted(
        ((similarity(c.name, g["name"]), i, j) for i, c in enumerate(components) for j, g in enumerate(groups)),
        reverse=True,
    )
    used_c, used_g = set(), set()

    def one_to_one(require_same_weight: bool) -> None:
        for sim, i, j in pairs:  # greedy best-first
            if sim < min_similarity or i in used_c or j in used_g:
                continue
            c, g = components[i], groups[j]
            same = abs(c.weight_pct - g["weight"]) < 0.01
            if require_same_weight and not same:
                continue
            used_c.add(i)
            used_g.add(j)
            rows.append(ReconRow(syllabus_component=c.name, syllabus_weight=c.weight_pct, canvas_group=g["name"],
                                 canvas_weight=g["weight"], status="match" if same else "weight_differs",
                                 similarity=round(sim, 2)))

    # Pass 1: similar name AND same weight. Pass 2: splits. Pass 3: similar name, different weight.
    one_to_one(require_same_weight=True)
    # One syllabus component split into several Canvas groups (e.g. "Midterm 25%" = two 12.5% groups):
    # the leftover groups must share a word with the component AND their weights must add up exactly.
    for i, c in enumerate(components):
        if i in used_c:
            continue
        parts = [j for j, g in enumerate(groups) if j not in used_g and _words(c.name) & _words(g["name"])]
        if len(parts) >= 2 and abs(sum(groups[j]["weight"] for j in parts) - c.weight_pct) < 0.01:
            used_c.add(i)
            for j in parts:
                used_g.add(j)
                rows.append(ReconRow(syllabus_component=c.name, syllabus_weight=c.weight_pct,
                                     canvas_group=groups[j]["name"], canvas_weight=groups[j]["weight"],
                                     status="split_in_canvas", similarity=round(similarity(c.name, groups[j]["name"]), 2)))
    one_to_one(require_same_weight=False)
    # Several syllabus components inside ONE Canvas group (e.g. "Case Dev 15%" + "Data Viz 15%" = "Group Projects 30%"):
    # each component must match an item name in that group, and their weights must add up to the group's exactly.
    for j, g in enumerate(groups):
        if j in used_g:
            continue
        item_names = [it[0] for it in g.get("items", [])]
        members = [i for i, c in enumerate(components) if i not in used_c
                   and any(similarity(c.name, n) >= 0.5 or _words(n) and _words(n) <= _words(c.name) for n in item_names)]
        if len(members) >= 2 and abs(sum(components[i].weight_pct for i in members) - g["weight"]) < 0.01:
            used_g.add(j)
            for i in members:
                used_c.add(i)
                rows.append(ReconRow(syllabus_component=components[i].name, syllabus_weight=components[i].weight_pct,
                                     canvas_group=g["name"], canvas_weight=g["weight"], status="merged_in_canvas",
                                     similarity=None))
    for i, c in enumerate(components):
        if i not in used_c:
            rows.append(ReconRow(syllabus_component=c.name, syllabus_weight=c.weight_pct, canvas_group=None,
                                 canvas_weight=None, status="only_in_syllabus", similarity=None))
    for j, g in enumerate(groups):
        if j not in used_g:
            rows.append(ReconRow(syllabus_component=None, syllabus_weight=None, canvas_group=g["name"],
                                 canvas_weight=g["weight"], status="only_in_canvas", similarity=None))
    return rows
