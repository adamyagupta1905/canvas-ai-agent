"""Local report over the real Canvas snapshot. Reads data/, writes data/ (both gitignored).

    .venv/bin/python -m scripts.course_report
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from canvas_agent.grades import (
    grade_summary, needed_on_remaining, prefix_topic, term_progress, topic_mastery, tracker,
)

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "canvas_snapshot.json"
OUT = ROOT / "data" / "course_report.md"

# How graded item names map to topics, per course: {"CODE": "regex with the topic as group 1" | null}.
# Course-specific, so it lives in data/course_topics.json (gitignored), not in code.
TOPICS_FILE = ROOT / "data" / "course_topics.json"


def main(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    courses = json.loads(SNAP.read_text())["courses"]
    lines = [f"# Course report (snapshot {json.loads(SNAP.read_text())['_meta']['captured_at']}, run {now:%Y-%m-%d})", ""]
    all_rows = []
    topics = json.loads(TOPICS_FILE.read_text()) if TOPICS_FILE.exists() else {}
    for code, c in courses.items():
        s = grade_summary(code, c)
        rows = tracker(code, c, now)
        all_rows += rows
        lines += [f"## {code}: {c['name']}", ""]
        lines.append(f"- **Current grade: {s.current_pct if s.current_pct is not None else 'n/a'}%**"
                     + (f" (from components worth {s.weight_determined}% of the course)" if s.weight_determined is not None else " (points total)"))
        lines.append(f"- If nothing else were submitted (Canvas 'final' view): {s.canvas_final_pct}%")
        lines.append(f"- Possible finishing range: {s.floor_pct}% (all remaining = 0) to {s.ceiling_pct}% (all remaining = 100%)")
        for target in ((70, 80, 90) if c["weighted"] else ()):
            n = needed_on_remaining(c, target)
            if n is not None:
                lines.append(f"- To finish at {target}%: average {n}% on everything remaining" + (" (out of reach)" if n > 100 else ""))
        if s.canvas_check:
            ck = s.canvas_check
            lines.append(f"- Check against Canvas: current {ck['ours_current']} vs {ck['canvas_current']}, "
                         f"final {ck['ours_final']} vs {ck['canvas_final']} -> {'MATCH' if ck['match'] else 'MISMATCH'}")
        if not c["weighted"]:
            lines.append("- Targets not shown: without the syllabus weights a points total cannot say what you need")
        if not s.canvas_check:
            lines.append("- Check against Canvas: the instructor hides the course total, so this number cannot be cross-checked")
        lines += ["", "| Component | Weight | Counted | Dropped | Earned / Possible | Component % | Adds to grade |",
                  "|---|---|---|---|---|---|---|"]
        for g in s.groups:
            if c["weighted"] and not g.weight:
                continue  # 0% components (practice, tokens) cannot move the grade
            lines.append(f"| {g.name} | {g.weight if c['weighted'] else '-'} | {g.kept} | {', '.join(g.dropped) or '-'} | "
                         f"{g.earned:g} / {g.possible:g} | {g.pct if g.pct is not None else 'not yet'} | "
                         f"{g.contribution if g.contribution is not None else '-'} |")
        lines.append("")
        tp = term_progress(rows, now)
        lines.append(f"- Dated assessments already past: {tp if tp is not None else 'n/a'}%")
        counts = {}
        for r in rows:
            counts[r.status] = counts.get(r.status, 0) + 1
        lines.append(f"- Items: {len(rows)} total; " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        upcoming = sorted((r for r in rows if r.status == "upcoming"), key=lambda r: r.due)
        if upcoming:
            lines.append("- Upcoming: " + "; ".join(
                f"{r.name} ({r.due:%b %d}, {r.size}"
                + (f", {r.weight_of_final}% of grade" if r.weight_of_final else "") + ")" for r in upcoming))
        pd = [r.name for r in rows if r.status == "past_due_unsubmitted"]
        if pd:
            lines.append(f"- Past due and not submitted in Canvas (check whether submitted elsewhere): {', '.join(pd)}")
        if topics.get(code):
            m = topic_mastery(rows, prefix_topic(topics[code]))
            weak = sorted(m.items(), key=lambda kv: kv[1])[:5]
            lines.append("- Weakest topics by graded evidence: " + ", ".join(f"{t} {p}%" for t, p in weak))
        for w in s.warnings:
            lines.append(f"- Warning: {w}")
        lines.append("")
    OUT.write_text("\n".join(lines))
    (ROOT / "data" / "tracker.json").write_text(json.dumps([r.model_dump(mode="json") for r in all_rows], indent=1))
    return "\n".join(lines)


if __name__ == "__main__":
    print(main())
