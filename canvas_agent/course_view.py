"""The five views every course gets, built only from data we already have. Pure Python, no LLM.

    progress(store, code, now)   how far the course has got + grade summary
    notes(store, code, now)      structured notes from the start to what is released, in course order
    deadlines(store, code, now)  one deadline list merged from Canvas, PrairieLearn, the course website and exams
    plan(store, code, now)       a day-by-day plan: due work first, exam prep before each exam, weak areas named
    overview(store, now)         one row per course for the all-courses page

Notes are an outline of what the course released (lecture titles, learning goals, slide titles, module topics),
each linked to its source. They are extracted, not written, so they never contain claims the course did not make.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from canvas_agent.grades import grade_summary, term_progress, tracker
from canvas_agent.tools import Store

_MON = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_TZ_NAIVE_END = time(23, 59)


def local_tz():
    """Times written on course pages ('Fri 27 Nov, 18:00') are local course time; we use this machine's zone."""
    return datetime.now().astimezone().tzinfo


# ---- small helpers ------------------------------------------------------------------------------------------
def parse_day(text: str, today: date) -> date | None:
    """'Sep 8, 10' | 'Fri 27 Nov, 18:00' | 'Thu 29 Oct' -> a date in the academic year around `today`."""
    t = text.lower()
    m = re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})\b", t) or None
    d = None
    if m and m.group(1) in _MON:
        mon, day = _MON[m.group(1)], int(m.group(2))
    else:
        m = re.search(r"\b(\d{1,2})\s+([a-z]{3})", t)
        if not (m and m.group(2) in _MON):
            return None
        mon, day = _MON[m.group(2)], int(m.group(1))
    year = today.year + (1 if mon < today.month - 6 else -1 if mon > today.month + 6 else 0)
    try:
        d = date(year, mon, day)
    except ValueError:
        return None
    return d


def parse_clock(text: str) -> time | None:
    if re.search(r"\d\s*(am|pm)\b", text, re.I):
        return _clock_ampm(text)
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    return time(int(m.group(1)), int(m.group(2))) if m else None


def _clock_ampm(text: str) -> time | None:
    """'11:59 pm' -> 23:59 (parse_clock already handles 24-hour times)."""
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.I)
    if not m:
        return None
    return time(int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0), int(m.group(2) or 0))


def _keys(name: str) -> set[str]:
    """Matching keys so 'D4', 'DELIV4 Build v3' and 'Deliverable 4' meet, and 'LAB09' meets 'Lab 9'."""
    n = name.lower()
    keys = set()
    for pre, num in re.findall(r"\b(d|deliv|deliverable|lab|hw|homework|midterm|final|quiz|tu|tutorial)\s*0*(\d*)\b", n):
        base = {"deliv": "d", "deliverable": "d", "homework": "hw", "tutorial": "tu"}.get(pre, pre)
        keys.add(f"{base}{num}")
    return keys


def _as_dt(d: date, t: time | None) -> datetime:
    return datetime.combine(d, t or _TZ_NAIVE_END, tzinfo=local_tz())


# ---- progress -----------------------------------------------------------------------------------------------
def progress(store: Store, code: str, now: datetime) -> dict:
    from canvas_agent.component_check import count_sessions
    from canvas_agent.website import find_doc, schedule_rows

    course = store.courses()[code]
    facts = store.facts(code) or {}
    rows = tracker(code, course, now)
    out: dict = {"code": code, "name": course["name"], "dated_items_past_pct": term_progress(rows, now)}

    view = facts.get("syllabus_view_grade")
    g = view or grade_summary(code, course).model_dump()
    out["grade"] = {"current_pct": g["current_pct"], "weight_graded_pct": g["weight_determined"],
                    "floor_pct": g["floor_pct"], "ceiling_pct": g["ceiling_pct"],
                    "basis": "syllabus weights and rules" if view else "Canvas setup"}
    out["needed"] = facts.get("needed")

    sessions = count_sessions(course)
    if sessions:
        held = max((int(m.group(1)) for r in rows if r.due and r.due <= now
                    and (m := re.search(r"class\D{0,3}(\d{1,2})", r.name, re.I))), default=None)
        out["sessions"] = {"held": held, "total": sessions.total, "source": sessions.source}
    doc = find_doc(store.root / "sites" / code, "schedule")
    if doc:
        weeks = [r for r in schedule_rows(doc) if r.get("Wk") or r.get("Week")]
        starts = [parse_day(r.get("Dates", "") or r.get("Date", ""), now.date()) for r in weeks]
        known = [s for s in starts if s]
        if known:
            out["weeks"] = {"passed": sum(s <= now.date() for s in known), "total": len(weeks), "source": doc.url}
    parts = []
    if out.get("weeks"):
        parts.append(out["weeks"]["passed"] / out["weeks"]["total"])
    elif out.get("sessions") and out["sessions"]["held"]:
        parts.append(out["sessions"]["held"] / out["sessions"]["total"])
    elif out["dated_items_past_pct"] is not None:
        parts.append(out["dated_items_past_pct"] / 100)
    out["course_progress_pct"] = round(100 * parts[0], 1) if parts else None
    return out


# ---- notes --------------------------------------------------------------------------------------------------
_WORDLIKE = re.compile(r"[A-Z]?[a-z]+(?:['’-][a-z]+)*[,.:?!]?|[A-Z]{2,6}[,.:?!]?|\d+[a-z%]*[,.:]?|&|-|—|–")


def _is_title(line: str) -> bool:
    """A slide title: starts with a capital or digit, 4-80 chars, not code, and mostly real-looking words
    (mirrored or rotated OCR text like 'ABojOuIudL' or 'S PIO v PIO' fails)."""
    if not (4 <= len(line) <= 80 and re.match(r"[A-Z0-9]", line) and re.search(r"[A-Za-z][a-z]{2}", line)):
        return False
    if re.search(r"[{}();=<>`\]\[|‘’¥®©]|=>|\bconst\b|\bfunction\b|\bimport\b", line):
        return False
    toks = line.split()
    good = [t for t in toks if _WORDLIKE.fullmatch(t)]
    vowels = all(re.search(r"[aeiouy]", t, re.I) for t in good if len(t) > 3 and t.isalpha())
    return len(good) / len(toks) >= 0.75 and vowels


def _deck_title(url: str) -> str:
    """'02a-coupling.pdf' -> '02a · Coupling'."""
    name = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    name = re.sub(r"[-_][0-9a-f]{16,}$", "", name)  # drop content hashes: 'Lec01-81957efe...' -> 'Lec01'
    lm = re.match(r"(?:lec|lecture)[-_ ]?0*(\d+)$", name, re.I)
    if lm:
        return f"Lecture {lm.group(1)}"
    m = re.match(r"(\d+[a-z]?)[-_ ]+(.*)", name)
    words = (m.group(2) if m else name).replace("_", " ").replace("-", " ").strip()
    return f"{m.group(1)} · {words[:1].upper() + words[1:]}" if m else words[:1].upper() + words[1:]


def _slide_outline(chunks: list[dict]) -> dict:
    """Title, learning goals and slide titles for one slide deck, from its index chunks (first chunk per page)."""
    pages: dict[int, str] = {}
    for c in chunks:
        pages.setdefault(c["page"], c["text"])
    n = max(len(pages), 1)
    common = Counter(l.strip() for t in pages.values() for l in set(t.splitlines()) if l.strip())
    boiler = {l for l, k in common.items() if k / n >= 0.3 and n >= 4}
    def lines(t):
        return [l.strip(" ●•-\t") for l in t.splitlines() if l.strip() and l.strip() not in boiler]
    ordered = [lines(pages[p]) for p in sorted(pages)]
    first_lines = ordered[0][:4] if ordered and ordered[0] else []
    first = first_lines[0] if first_lines else ""
    title = ""
    if re.match(r"^\d+[a-z]?\s*[-–:]\s*\S", first):  # e.g. '02a - Cost of Change: Coupling'
        title = first
    elif re.match(r"^(lecture|lec|class)\s*\d+\s*:?\s*$", first, re.I) and len(first_lines) > 1:
        title = (first.rstrip(" :") + ": " + " ".join(first_lines[1:3])).strip()  # 'Lecture 1:' + next lines
    elif re.match(r"^(lecture|lec|class)\s*\d+\s*[:\-–]\s*\S", first, re.I):
        title = first
    goals, titles = [], []
    for ls in ordered[1:]:
        if not ls:
            continue
        head = ls[0]
        if re.match(r"learning (goals|objectives|outcomes)", head, re.I):
            joined: list[str] = []
            for l in ls[1:]:
                if re.match(r"by the end", l, re.I):
                    continue
                if joined and (l[:1].islower() or not re.search(r"[.!?:]$", joined[-1]) and l[:1].islower()):
                    joined[-1] = f"{joined[-1]} {l}"  # wrapped line continues the previous goal
                else:
                    joined.append(l)
            goals += [g for g in joined if len(g) > 15][:6]
        elif _is_title(head) and head not in titles:
            titles.append(head)
    seen, uniq = set(), []
    for g in goals:  # decks often repeat the goals slide at the end
        k = g.rstrip(". ").lower()
        if k not in seen:
            seen.add(k)
            uniq.append(g)
    return {"title": title, "goals": uniq, "slides": titles, "pages": len(pages)}


def notes(store: Store, code: str, now: datetime) -> list[dict]:
    """Sections in course order. Website slide decks if the course has them, else Canvas modules."""
    import json

    sections: list[dict] = []
    idx_path = store.root / "sites" / code / "index.json"
    if idx_path.exists():
        idx = json.loads(idx_path.read_text())
        decks: dict[str, list] = {}
        for c in idx:
            if c["kind"] == "lecture":
                decks.setdefault(c["url"], []).append(c)
        def _num(u: str):
            n = re.search(r"(\d+)", u.rsplit("/", 1)[-1])
            return (int(n.group(1)) if n else 999, u.rsplit("/", 1)[-1])

        for url in sorted(decks, key=_num):
            o = _slide_outline(decks[url])
            fname = url.rsplit("/", 1)[-1]
            m = None if re.match(r"(lec|lecture)", fname, re.I) else re.match(r"(\d+)", fname)  # leading digits = week
            sections.append({"order": url.rsplit("/", 1)[-1], "week": int(m.group(1)) if m else None,
                             "title": o["title"] or _deck_title(url), "goals": o["goals"],
                             "points": o["slides"], "source": url, "kind": "lecture slides", "scores": []})
    if sections:
        return sections

    course = store.courses().get(code, {})
    graded = {r.name.strip().lower(): r.pct for r in tracker(code, course, now) if r.pct is not None}
    for name, items in course.get("modules", []):
        topics = [i[2:].strip() for i in items if i[:2] in ("P:", "S:", "F:", "E:")]
        if not topics:
            continue
        scores = [{"item": i[2:].strip(), "pct": graded[i[2:].strip().lower()]} for i in items
                  if i[:2] in ("A:", "Q:") and i[2:].strip().lower() in graded]
        sections.append({"order": len(sections), "week": None, "title": name, "goals": [],
                         "points": list(dict.fromkeys(re.sub(r"\.(pdf|docx?|pptx?|xlsx?)$", "", t, flags=re.I)
                                                      for t in topics)),
                         "source": "Canvas Modules", "kind": "module", "scores": scores})
    return sections


# ---- deadlines ----------------------------------------------------------------------------------------------
def deadlines(store: Store, code: str, now: datetime, include_past_days: int = 0) -> list[dict]:
    """Merged from every source. Same day + matching key (e.g. 'D4' and 'DELIV4') = one entry with both sources."""
    from canvas_agent.website import find_doc, schedule_rows

    course = store.courses()[code]
    facts = store.facts(code) or {}
    raw: list[dict] = []
    for r in tracker(code, course, now):
        if r.due and r.status in ("upcoming", "past_due_unsubmitted", "awaiting_grade"):
            raw.append({"when": r.due, "what": r.name, "source": "Canvas", "pct_of_grade": r.weight_of_final,
                        "status": r.status, "has_time": True})
    for a in store.pl():
        if a.course == code and a.deadline and a.kind in ("graded", "tutorial"):
            raw.append({"when": a.deadline.astimezone(timezone.utc), "what": f"{a.label} {a.title}", "source": "PrairieLearn",
                        "pct_of_grade": None, "status": a.status, "has_time": True})
        if a.course == code and a.available_from:
            raw.append({"when": a.available_from.astimezone(timezone.utc), "what": f"{a.label} {a.title} opens",
                        "source": "PrairieLearn", "pct_of_grade": None, "status": "not_yet_available", "has_time": True})
    weights = {c["component"].lower(): c["syllabus_weight"] for c in facts.get("component_checks", [])}
    from canvas_agent.website import deadline_rows

    doc = find_doc(store.root / "sites" / code, "schedule")
    for r in (deadline_rows(doc) if doc else []):
        d = parse_day(r["when"], now.date())
        if not d:
            continue
        clock = parse_clock(r["when"]) or _clock_ampm(r["when"])
        is_exam = bool(re.search(r"midterm|final|exam", r["what"], re.I))
        first = r["what"].lower().split()[0] if r["what"] else ""
        w = next((v for k, v in weights.items() if is_exam and first and first in k), None)
        raw.append({"when": _as_dt(d, clock), "what": r["what"], "source": "course website", "pct_of_grade": w,
                    "status": "exam" if is_exam else "scheduled", "has_time": bool(clock)})
    for e in facts.get("facts", {}).get("exams", []):
        d = date.fromisoformat(e["exam_date"]) if e.get("exam_date") else None
        hint = next((h for h in facts.get("exam_date_hints", []) if h["kind"] == e["kind"]), None)
        d = d or (date.fromisoformat(hint["found_date"]) if hint else None)
        if d:
            w = next((v for k, v in weights.items() if e["kind"] in k or e["title"].lower() in k), None)
            raw.append({"when": _as_dt(d, None), "what": e["title"], "source": "syllabus", "pct_of_grade": w,
                        "status": "exam", "has_time": False})
    cutoff = now - timedelta(days=include_past_days)
    raw = [r for r in raw if r["when"] >= cutoff or r["status"] == "past_due_unsubmitted"]
    merged: list[dict] = []
    for r in sorted(raw, key=lambda r: r["when"]):
        twin = next((m for m in merged if m["when"].astimezone(local_tz()).date() == r["when"].astimezone(local_tz()).date()
                     and ((_keys(m["what"]) & _keys(r["what"])) or (m["status"] == r["status"] == "exam"))), None)
        if twin:
            if r["source"] not in twin["sources"]:
                twin["sources"].append(r["source"])
            twin["pct_of_grade"] = twin["pct_of_grade"] or r["pct_of_grade"]
            if r["status"] == "exam":
                twin["status"] = "exam"
            if r["has_time"] and not twin["has_time"]:
                twin["when"], twin["has_time"] = r["when"], True
            continue
        merged.append({**r, "sources": [r.pop("source")]})
    for m in merged:
        m.pop("source", None)
        m["when"] = m["when"].astimezone(local_tz())
        m["days_left"] = round((m["when"] - now).total_seconds() / 86400, 1)
    return merged


# ---- plan ---------------------------------------------------------------------------------------------------
def plan(store: Store, code: str, now: datetime, days: int | None = None) -> dict:
    """Rules, in order:
    1. Every open deadline in the window gets a work block the day before it is due (today if that has passed).
    2. Every upcoming exam gets review blocks 7, 4, 2 and 1 days before it (only those still ahead).
    3. Review blocks rotate through weak areas: the student's own improvement areas first, then the lowest-scoring
       components, then 'all topics so far'.
    Items already submitted / awaiting a grade are not planned."""
    from canvas_agent.user_inputs import InputStore

    tz = local_tz()
    today = now.astimezone(tz).date()
    all_dl = deadlines(store, code, now)
    last = max((d["when"].date() for d in all_dl), default=today)
    end = today + timedelta(days=days) if days is not None else last  # default: plan to the last deadline/exam
    facts = store.facts(code) or {}
    weak = [a for e in InputStore(store.root / "user_inputs.json").for_course(code) for a in e.improvement_areas]
    low = sorted((c for c in facts.get("component_checks", []) if c.get("avg_syllabus_rules") is not None
                  and c["avg_syllabus_rules"] < 75), key=lambda c: c["avg_syllabus_rules"])
    weak += [f"{c['component']} (average {c['avg_syllabus_rules']:.0f}%)" for c in low]
    # then the course's own units in order, so review covers the whole course (lecture decks or modules with content)
    units = [n["title"] for n in notes(store, code, now) if n["kind"] == "lecture slides" or n["scores"] or len(n["points"]) >= 3]
    weak = weak + [u for u in units if u not in weak]
    weak = weak or ["all topics so far"]

    days_map: dict[date, list[dict]] = {}
    def add(d: date, task: str, why: str):
        if today <= d <= end:
            days_map.setdefault(d, []).append({"task": task, "why": why})

    rotation = 0
    for dl in all_dl:
        if dl["status"] in ("awaiting_grade", "not_yet_available"):
            continue
        due = dl["when"].date()  # already local
        if dl["status"] == "exam":
            for back in (7, 4, 2, 1):
                topic = weak[rotation % len(weak)]
                rotation += 1
                add(due - timedelta(days=back), f"Review for {dl['what']}: {topic}",
                    f"{dl['what']} on {due:%a %b %d}" + (f" ({dl['pct_of_grade']:g}% of grade)" if dl["pct_of_grade"] else ""))
            add(due, f"Exam: {dl['what']}", "exam day")
        elif due <= end:
            work_day = max(today, due - timedelta(days=1))
            add(work_day, f"Work on: {dl['what']}", f"due {dl['when']:%a %b %d %H:%M}" if dl["has_time"] else f"due {due:%a %b %d}")
    return {"code": code, "from": today.isoformat(), "to": end.isoformat(), "weak_areas": weak[:len(weak) - len(units)] or weak[:1],
            "days": [{"date": d.isoformat(), "tasks": t} for d, t in sorted(days_map.items())]}


# ---- all courses --------------------------------------------------------------------------------------------
def overview(store: Store, now: datetime) -> list[dict]:
    rows = []
    for code in store.courses():
        p = progress(store, code, now)
        nxt = next((d for d in deadlines(store, code, now) if d["status"] not in ("awaiting_grade", "not_yet_available")
                    and d["when"] >= now), None)
        rows.append({"code": code, "name": p["name"], "grade_pct": p["grade"]["current_pct"],
                     "graded_weight_pct": p["grade"]["weight_graded_pct"], "basis": p["grade"]["basis"],
                     "course_progress_pct": p["course_progress_pct"],
                     "next_deadline": f"{nxt['what']} ({nxt['when']:%b %d})" if nxt else None})
    return rows
