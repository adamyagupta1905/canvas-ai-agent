"""Per-component validation: syllabus rules vs what Canvas actually contains. Pure Python, no LLM.

For each syllabus component (e.g. "In-class exercises 10%") this works out:
  - which Canvas items belong to it (via the group reconcile, or item-by-item for unweighted courses)
  - how many items the syllabus says exist vs how many Canvas has (and numbering gaps like a missing Class 13)
  - the drop rule in the syllabus vs the drop rule configured in Canvas
  - whether the syllabus combines items by points or weights each item equally, and whether that changes the average
  - the component average now under Canvas's rules AND under the syllabus's rules
It also builds a "syllabus view" course (syllabus weights + syllabus rules + placeholders for items the
syllabus promises but Canvas has not created yet) that the existing grade engine can score.
"""

from __future__ import annotations

import re
from statistics import median

from pydantic import BaseModel

from canvas_agent.grades import _apply_drops, _item_keys
from canvas_agent.reconcile import ReconRow, _words
from canvas_agent.syllabus_flow import ComponentRule, GradeComponent


class ItemRow(BaseModel):
    name: str
    points: float | None
    score: float | None
    pct: float | None
    dropped_syllabus: bool = False
    dropped_canvas: bool = False


class SessionCount(BaseModel):
    """How many class sessions the course has, counted by Python from Canvas Modules titles (Class 01..N)."""
    total: int
    numbers: list[int]
    with_assessed_item: list[int]  # sessions whose module contains an Assignment/Quiz
    source: str


def count_sessions(course: dict) -> SessionCount | None:
    """Count sessions from module item titles like 'Class 07: ...'. Needs >= 5 distinct numbers to trust it."""
    seen: set[int] = set()
    assessed: set[int] = set()
    for _, items in course.get("modules", []):
        for it in items:
            m = re.search(r"\bclass\s*0?(\d{1,2})\b", it, re.I)
            if not m:
                continue
            n = int(m.group(1))
            seen.add(n)
            if it[:2] in ("A:", "Q:"):
                assessed.add(n)
    if len(seen) < 5:
        return None
    return SessionCount(total=max(seen), numbers=sorted(seen), with_assessed_item=sorted(assessed),
                        source=f"Canvas Modules titles (Class {min(seen):02d}-{max(seen):02d})")


class AttendanceCheck(BaseModel):
    status: str  # "confirmed" (student said these are absences) | "possible" (zero scores, unverified)
    thresholds_pct: list[float]  # e.g. [15, 20] from the syllabus wording "missing more than X%"
    allowed_absences: dict[str, int]  # threshold -> most absences that stay within it (Python: floor)
    sessions_total: int
    sessions_held: int | None  # highest class number with a released mark
    sessions_remaining: int | None
    possible_absences: list[str]  # kept name for compatibility: the absence list (confirmed or possible)
    possible_absence_pct: float
    exceeds: list[float]
    quotes: list[str]
    caveat: str


class ComponentCheck(BaseModel):
    component: str
    syllabus_weight: float
    canvas_groups: list[str]
    canvas_weight: float | None
    items_in_canvas: int
    scored: int
    expected_items: int | None
    expected_source: str | None = None  # where the expected count came from
    sessions_total: int | None = None
    remaining_by_syllabus: int | None
    numbering_gaps: list[int]
    drop_syllabus: int | None
    never_drop: list[str] = []  # item keys the syllabus says can never be dropped, e.g. ['hw5']
    drop_canvas: int | None
    aggregation_syllabus: str | None
    equal_points_in_canvas: bool
    completion_based: bool
    avg_canvas_rules: float | None
    avg_syllabus_rules: float | None
    items: list[ItemRow]
    flags: list[str]
    rules: list[str]  # plain-language rules with their source, for the dashboard


class ItemMapping(BaseModel):
    item: str
    component: str | None
    shared_words: list[str]
    confidence: str  # high | low | ambiguous | unmapped


def _rule(rules: list[ComponentRule], comp: str, kind: str) -> ComponentRule | None:
    for r in rules:
        if r.component == comp and r.kind == kind:
            return r
    return None


_EXAM_WORDS = {"midterm", "final"}


def map_items_to_components(components: list[GradeComponent], course: dict,
                            overrides: dict[str, str | None] | None = None) -> list[ItemMapping]:
    """For courses where Canvas has no weights: assign each Canvas item to a syllabus component.

    confidence:
      confirmed  - the student said so (overrides file); always used
      high       - item shares >= 60% of the component's words, or is the only item naming midterm/final
      low        - some shared words; NOT counted in the syllabus-view grade until the student confirms
      ambiguous  - ties between components; not counted
      unmapped   - nothing in common; not counted
    """
    overrides = overrides or {}
    omit = set(course.get("omit_from_final", []))
    all_items = [it[0] for g in course["groups"] for it in g["items"] if it[0] not in omit]
    exam_item_count = {w: sum(1 for n in all_items if w in _words(n)) for w in _EXAM_WORDS}
    out = []
    for g in course["groups"]:
        for it in g["items"]:
            name = it[0]
            if name in omit:
                continue
            if name in overrides:
                out.append(ItemMapping(item=name, component=overrides[name], shared_words=[],
                                       confidence="confirmed" if overrides[name] else "unmapped"))
                continue
            iw = _words(name)
            scored = sorted(((len(iw & _words(c.name)), c.name) for c in components), reverse=True)
            best, comp = scored[0] if scored else (0, None)
            if best == 0:
                out.append(ItemMapping(item=name, component=None, shared_words=[], confidence="unmapped"))
                continue
            if len(scored) > 1 and scored[1][0] == best:
                out.append(ItemMapping(item=name, component=None, shared_words=[], confidence="ambiguous"))
                continue
            cw = _words(comp)
            shared = sorted(iw & cw)
            unique_exam = any(w in shared and exam_item_count[w] == 1 for w in _EXAM_WORDS)
            conf = "high" if (len(shared) / len(cw) >= 0.6 or unique_exam) else "low"
            out.append(ItemMapping(item=name, component=comp, shared_words=shared, confidence=conf))
    return out


def _numbering_gaps(names: list[str]) -> list[int]:
    nums = sorted({int(m.group(1)) for n in names if (m := re.search(r"(?:class|week|lab|tutorial|quiz|hw)\D{0,3}(\d{1,2})", n, re.I))})
    if len(nums) < 3:
        return []
    return [k for k in range(nums[0], nums[-1] + 1) if k not in nums]


def _avg(pairs: list[tuple[float, float, str]], aggregation: str | None) -> float | None:
    if not pairs:
        return None
    if aggregation == "equal_weight":
        pcts = [s / p for s, p, _ in pairs if p]
        return round(100 * sum(pcts) / len(pcts), 2) if pcts else None
    e, p = sum(s for s, _, _ in pairs), sum(p for _, p, _ in pairs)
    return round(100 * e / p, 2) if p else None


_SESSION_WORDS = re.compile(r"\b(in-?class|class|lecture|session|tutorial)\b", re.I)


def check_components(components: list[GradeComponent], rules: list[ComponentRule], course: dict,
                     recon: list[ReconRow], overrides: dict[str, str | None] | None = None) -> list[ComponentCheck]:
    omit = set(course.get("omit_from_final", []))
    sessions = count_sessions(course)
    groups_by_name = {g["name"]: g for g in course["groups"]}
    mapping = map_items_to_components(components, course, overrides) if not course["weighted"] else []
    out: list[ComponentCheck] = []
    for comp in components:
        if course["weighted"]:
            gnames = [r.canvas_group for r in recon if r.syllabus_component == comp.name and r.canvas_group
                      and r.status in ("match", "weight_differs", "split_in_canvas")]
            merged = [r.canvas_group for r in recon if r.syllabus_component == comp.name and r.status == "merged_in_canvas"]
            raw = [it for gn in gnames for it in groups_by_name[gn]["items"] if it[0] not in omit]
            for gn in merged:  # only the items of this group that belong to this component
                raw += [it for it in groups_by_name[gn]["items"] if it[0] not in omit
                        and (_words(it[0]) and _words(it[0]) <= _words(comp.name) or len(_words(it[0]) & _words(comp.name)) >= 2)]
            cweight = sum(groups_by_name[gn]["weight"] for gn in gnames) if gnames else (comp.weight_pct if merged else None)
            gnames = gnames + merged
            canvas_drop = sum(int((groups_by_name[gn].get("rules") or {}).get("drop_lowest") or 0) for gn in gnames) if gnames else None
        else:
            names = {m.item for m in mapping if m.component == comp.name and m.confidence in ("high", "confirmed")}
            pending = [m.item for m in mapping if m.component == comp.name and m.confidence == "low"]
            raw = [it for g in course["groups"] for it in g["items"] if it[0] in names]
            gnames, cweight, canvas_drop = [], None, None

        r_count = _rule(rules, comp.name, "item_count")
        r_counted = _rule(rules, comp.name, "counted_items")
        r_drop = _rule(rules, comp.name, "drop_lowest")
        r_agg = _rule(rules, comp.name, "aggregation")
        r_comp = _rule(rules, comp.name, "completion_based")
        expected = int(r_count.value) if r_count and r_count.value is not None else None
        expected_source = f"syllabus: \"{r_count.source_quote}\"" if expected is not None else None
        sessions_total = None
        session_based = bool(_SESSION_WORDS.search(comp.name))
        if expected is None and session_based and sessions:
            # A class "has an item" if Canvas has an item numbered for it OR its module lists one.
            in_canvas = {int(m.group(1)) for it in raw if (m := re.search(r"class\D{0,3}(\d{1,2})", it[0], re.I))}
            covered = in_canvas | set(sessions.with_assessed_item)
            no_item = [n for n in range(1, sessions.total + 1) if n not in covered]
            expected = sessions.total - len(no_item)
            sessions_total = sessions.total
            expected_source = (f"counted by Python from {sessions.source}: {sessions.total} classes, "
                               f"{len(no_item)} with no assessed item {no_item}")
        drop_syl = int(r_drop.value) if r_drop and r_drop.value is not None else None
        if drop_syl is None and r_counted and r_counted.value is not None and expected:
            drop_syl = expected - int(r_counted.value)
        agg = r_agg.aggregation if r_agg else None
        protect = sorted(never_drop_keys([r.source_quote for r in rules if r.component == comp.name]))
        drop_rules = {"drop_lowest": drop_syl or 0, "never_drop_keys": protect}

        scored_pairs = [(float(it[4]), float(it[2] or 0), it[0]) for it in raw if it[4] is not None]
        pts = {float(it[2]) for it in raw if it[2]}
        equal_points = len(pts) <= 1

        canvas_kept, canvas_dropped = _apply_drops(scored_pairs, {"drop_lowest": canvas_drop or 0})
        if agg == "equal_weight":  # equal weighting == every item rescaled to the same points
            rescaled = [(s / p * 100, 100.0, n) for s, p, n in scored_pairs if p]
            syl_kept, syl_dropped = _apply_drops(rescaled, drop_rules)
        else:
            syl_kept, syl_dropped = _apply_drops(scored_pairs, drop_rules)

        items = [ItemRow(name=it[0], points=it[2], score=it[4],
                         pct=round(100 * it[4] / it[2], 1) if it[4] is not None and it[2] else None,
                         dropped_syllabus=it[0] in syl_dropped, dropped_canvas=it[0] in canvas_dropped) for it in raw]

        flags: list[str] = []
        gaps = _numbering_gaps([it[0] for it in raw])
        if sessions_total and gaps:
            unassessed = [g for g in gaps if g in sessions.numbers and g not in sessions.with_assessed_item]
            if unassessed:
                flags.append(f"No item for class(es) {unassessed}: the module for that class has no assessed item, "
                             "so this is by design, not a missing grade.")
                gaps = [g for g in gaps if g not in unassessed]
        if expected_source and expected_source.startswith("counted by Python"):
            flags.append(f"Expected item count {expected} was {expected_source}.")
        if expected is not None and expected != len(raw):
            who = "Syllabus says" if expected_source and expected_source.startswith("syllabus") else "Expected"
            flags.append(f"{who} {expected} items; Canvas has {len(raw)}"
                         + (f" (numbering gaps: {gaps})" if gaps else "") + ".")
        elif gaps:
            flags.append(f"Item numbering has gaps: {gaps}.")
        if course["weighted"] and drop_syl is not None and canvas_drop is not None and drop_syl != canvas_drop:
            flags.append(f"Syllabus drops {drop_syl}, Canvas is set to drop {canvas_drop}.")
        if drop_syl is None and canvas_drop:
            flags.append(f"Canvas drops {canvas_drop} but no drop rule was found in the syllabus.")
        a_c, a_s = _avg(canvas_kept, None), _avg(syl_kept, agg)
        if agg == "equal_weight" and not equal_points and a_c is not None and a_s is not None and abs(a_c - a_s) >= 0.01:
            flags.append(f"Syllabus weights items equally but the items have different points: "
                         f"{a_s}% (equal weighting, used) vs {a_c}% (by points).")
        if course["weighted"] and cweight is not None and abs(cweight - comp.weight_pct) >= 0.01:
            flags.append(f"Weight differs: syllabus {comp.weight_pct}%, Canvas {cweight}%.")
        if not course["weighted"] and pending:
            flags.append(f"Not counted until you confirm they belong here: {pending}.")
        if not raw:
            flags.append("No Canvas items found for this component yet.")
        zeros = sum(1 for s, _, _ in scored_pairs if s == 0)
        if r_comp and zeros:
            flags.append(f"{zeros} zero score(s) on a completion-marked component: usually missed or not submitted, not wrong answers.")

        out.append(ComponentCheck(
            component=comp.name, syllabus_weight=comp.weight_pct, canvas_groups=gnames, canvas_weight=cweight,
            items_in_canvas=len(raw), scored=len(scored_pairs), expected_items=expected,
            expected_source=expected_source, sessions_total=sessions_total,
            remaining_by_syllabus=(expected - len(scored_pairs)) if expected is not None else None,
            numbering_gaps=gaps, drop_syllabus=drop_syl, never_drop=protect, drop_canvas=canvas_drop, aggregation_syllabus=agg,
            equal_points_in_canvas=equal_points, completion_based=bool(r_comp),
            avg_canvas_rules=a_c, avg_syllabus_rules=a_s, items=items, flags=flags,
            rules=[f"{r.kind}: {r.summary} (\"{r.source_quote}\", {r.chunk_id})" for r in rules if r.component == comp.name],
        ))
    return out


def syllabus_view_course(components: list[GradeComponent], checks: list[ComponentCheck], course: dict) -> dict:
    """A course dict using syllabus weights and rules, ready for grades.grade_summary().

    - equal-weight components: every item rescaled to 100 points (exactly equal weighting)
    - syllabus drop rule applied
    - items the syllabus promises but Canvas has not created yet are added as unscored placeholders
      (median points of the existing items), so floor/ceiling/needed cover the whole term
    """
    groups = []
    for comp, chk in zip(components, checks):
        rows = [[i.name, None, i.points, "graded" if i.score is not None else "unsubmitted", i.score, False] for i in chk.items]
        if chk.aggregation_syllabus == "equal_weight":
            rows = [[n, d, 100.0 if p else p, st, (s / p * 100) if (s is not None and p) else s, l] for n, d, p, st, s, l in rows]
        if chk.expected_items and chk.expected_items > len(rows) and rows:
            typical = median([r[2] for r in rows if r[2]] or [0])
            rows += [[f"{comp.name} (not yet in Canvas) #{k + 1}", None, typical, "unsubmitted", None, False]
                     for k in range(chk.expected_items - len(rows))]
        rules = {"drop_lowest": chk.drop_syllabus} if chk.drop_syllabus else {}
        if rules and chk.never_drop:
            rules["never_drop_keys"] = chk.never_drop
        groups.append({"name": comp.name, "weight": comp.weight_pct, "rules": rules, "items": rows})
    return {"name": course.get("name", ""), "weighted": True, "omit_from_final": [], "groups": groups}


def attendance_check(checks: list[ComponentCheck], quotes: list[str], course: dict,
                     confirmed_absences: list[str] | None = None) -> AttendanceCheck | None:
    """Compare possible absences with the attendance thresholds the syllabus states.

    Possible absences = zero scores on a session-based component (e.g. in-class exercises). A zero can also
    be a wrong answer, so this is a warning to verify, not a finding.
    """
    sessions = count_sessions(course)
    rel = list(dict.fromkeys(q for q in quotes if re.search(r"miss", q, re.I) and re.search(r"class|attend", q, re.I)))
    thresholds = sorted({float(x) for q in rel for x in re.findall(r"more than (\d+(?:\.\d+)?)\s*%", q)})
    session_checks = [c for c in checks if _SESSION_WORDS.search(c.component) and c.items]
    if not (sessions and thresholds and session_checks):
        return None
    import math

    zeros = [i.name for c in session_checks for i in c.items if i.score == 0]
    absences = list(confirmed_absences) if confirmed_absences is not None else zeros
    status = "confirmed" if confirmed_absences is not None else "possible"
    pct = round(100 * len(absences) / sessions.total, 1)
    held_nums = [int(m.group(1)) for c in session_checks for i in c.items
                 if i.score is not None and (m := re.search(r"class\D{0,3}(\d{1,2})", i.name, re.I))]
    held = max(held_nums) if held_nums else None
    return AttendanceCheck(
        status=status, thresholds_pct=thresholds,
        # "missing more than X%" -> the largest whole number of absences still within X% of all classes
        allowed_absences={f"{t:g}": math.floor(t / 100 * sessions.total + 1e-9) for t in thresholds},
        sessions_total=sessions.total, sessions_held=held,
        sessions_remaining=(sessions.total - held) if held is not None else None,
        possible_absences=absences, possible_absence_pct=pct, exceeds=[t for t in thresholds if pct > t], quotes=rel,
        caveat=("Confirmed by you." if status == "confirmed" else
                "A zero may be a wrong or incomplete answer rather than an absence; check with your attendance record."),
    )


_MONTHS = ("january february march april may june july august september october november december "
           "jan feb mar apr jun jul aug sep sept oct nov dec").split()
_MONTH_NUM = {name: i % 12 + 1 for i, name in enumerate(_MONTHS[:12])}
_MONTH_NUM.update({"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
                   "oct": 10, "nov": 11, "dec": 12})
_DATE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d\d))?\b", re.I)
_EXAM_KIND = {"midterm": r"midterm", "final": r"final exam|final examination", "oral": r"viva|oral exam"}


class ExamDateHint(BaseModel):
    kind: str
    found_date: str  # ISO date parsed by Python
    quote: str
    source: str  # "syllabus" or "Canvas Modules"


def exam_date_hints(exams: list[dict], syllabus_text: str, course: dict, year: int) -> list[ExamDateHint]:
    """For exams the extraction left without a date, scan the syllabus text and Canvas module titles for a
    sentence that names that exam and a calendar date. Python parses the date; the quote shows where it came from.
    Shown as a hint to confirm, never written into the extracted facts."""
    from datetime import date as _date

    missing = {e["kind"] for e in exams if not e.get("exam_date")} & set(_EXAM_KIND)
    # "Nov. 16" must not be split into two sentences at the abbreviation's full stop
    text = re.sub(r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.", r"\1", syllabus_text)
    sentences = [("syllabus", s.strip()) for s in re.split(r"(?<=[.!?])\s+|\n", text) if s.strip()]
    sentences += [("Canvas Modules", t.split(":", 1)[1] if ":" in t[:3] else t)
                  for _, items in course.get("modules", []) for t in items]
    sentences += [("Canvas Modules", f"{k} exam: {v}") for k, v in (course.get("exam_dates_from_modules") or {}).items()]
    hints: list[ExamDateHint] = []
    for kind in sorted(missing):
        for source, s in sentences:
            if not re.search(_EXAM_KIND[kind], s, re.I):
                continue
            m = _DATE.search(s)
            if not m:
                continue
            mon = _MONTH_NUM[m.group(1).lower().rstrip('.')]
            try:
                d = _date(int(m.group(3) or year), mon, int(m.group(2)))
            except ValueError:
                continue
            hints.append(ExamDateHint(kind=kind, found_date=d.isoformat(), quote=s[:240], source=source))
    seen, out = set(), []
    for h in hints:
        if (h.kind, h.found_date) not in seen:
            seen.add((h.kind, h.found_date))
            out.append(h)
    return out


def past_exams_without_score(exams: list[dict], hints: list[dict], checks: list[ComponentCheck], today: str,
                             tools: list[str] | None = None) -> list[str]:
    """Exams whose date has passed (from the extraction or a Python date hint) but whose component has no score."""
    dates: dict[str, str] = {}
    for e in exams:
        if e.get("exam_date"):
            dates.setdefault(e["kind"], e["exam_date"])
    for h in hints:
        dates.setdefault(h["kind"], h["found_date"])
    out = []
    for kind, d in dates.items():
        if d >= today:
            continue
        comp = next((c for c in checks if kind in c.component.lower() or (kind == "oral" and "viva" in c.component.lower())), None)
        if comp and comp.scored == 0:
            where = f" Check {', '.join(tools)}." if tools else ""
            out.append(f"{comp.component} ({comp.syllabus_weight:g}%) was scheduled on or around {d} but has no score in Canvas or "
                       f"PrairieLearn yet.{where}")
    return out


_ITEM = r"(?:homework|hw|lab|quiz|assignment)\s*0*\d+"


def never_drop_keys(quotes: list[str]) -> set[str]:
    """'The lowest homework grade will be dropped, excluding Homework 5' -> {'hw5'}.
    Also: 'Homework 5 cannot be dropped', 'except Lab 1 and Lab 2'."""
    keys: set[str] = set()
    for q in quotes:
        for m in re.finditer(rf"(?:except|excluding|other than|not including|but not)\s+(?:for\s+)?((?:{_ITEM})(?:\s*(?:,|and|or)\s*(?:{_ITEM}|\d+))*)", q, re.I):
            chunk = m.group(1)
            base = re.match(r"(homework|hw|lab|quiz|assignment)", chunk, re.I).group(1)
            keys |= _item_keys(re.sub(r"(?<=,|d|r)\s*(\d+)\b", lambda x: f" {base} {x.group(1)}", chunk))
            keys |= _item_keys(chunk)
        for m in re.finditer(rf"({_ITEM})\s+(?:cannot|can't|may not|will not|won't|is not allowed to)\s+be dropped", q, re.I):
            keys |= _item_keys(m.group(1))
        for m in re.finditer(rf"({_ITEM})\s+(?:is|are)\s+(?:excluded|exempt)\s+from\s+(?:the\s+)?drop", q, re.I):
            keys |= _item_keys(m.group(1))
    return keys


class PassCheck(BaseModel):
    components: list[str]
    threshold_pct: float
    current_pct: float | None  # weighted average of the graded parts so far
    weight_graded: float
    weight_remaining: float
    needed_on_remaining_pct: float | None  # average needed on the ungraded parts to meet the threshold
    status: str  # met | safe | on_track | at_risk | unknown
    quote: str


def pass_requirement_checks(checks: list[ComponentCheck], rules: list[ComponentRule]) -> list[PassCheck]:
    """Pass requirements like 'at least 50% weighted average across midterms and final' or 'at least 40% homework
    average'. Rules with the same quote are one requirement spanning those components. Python does the maths:
    weighted average of graded components so far, and the average needed on ungraded ones (a component with some
    graded work counts at its current average)."""
    by_quote: dict[str, list[ComponentRule]] = {}
    for r in rules:
        if re.search(r"\bmiss|attend|absen|classes\b", r.source_quote, re.I):
            continue  # attendance thresholds ('missing more than 20% of classes') are handled by attendance_check
        if r.kind == "pass_requirement" and r.value is not None and 0 < r.value <= 100:
            by_quote.setdefault(r.source_quote.strip().lower(), []).append(r)
    out = []
    for rs in by_quote.values():
        names = list(dict.fromkeys(r.component for r in rs))
        comps = [c for c in checks if c.component in names]
        thr = rs[0].value
        graded = [c for c in comps if c.avg_syllabus_rules is not None]
        w_g = sum(c.syllabus_weight for c in graded)
        w_r = sum(c.syllabus_weight for c in comps if c.avg_syllabus_rules is None)
        cur = round(sum(c.syllabus_weight * c.avg_syllabus_rules for c in graded) / w_g, 2) if w_g else None
        if cur is None:
            need, status = None, "unknown"
        elif w_r == 0:
            partial = any(c.scored < max(c.items_in_canvas, c.expected_items or 0) for c in graded)
            need = None
            status = ("on_track" if partial else "met") if cur >= thr else "at_risk"  # work still to come: not final
        else:
            need = round((thr * (w_g + w_r) - cur * w_g) / w_r, 2)
            status = "safe" if need <= 0 else ("at_risk" if need > 100 else "on_track")
        out.append(PassCheck(components=names, threshold_pct=thr, current_pct=cur, weight_graded=w_g, weight_remaining=w_r,
                             needed_on_remaining_pct=need, status=status, quote=rs[0].source_quote))
    return out
