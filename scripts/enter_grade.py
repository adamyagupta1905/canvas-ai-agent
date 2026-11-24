"""Tell the agent a grade it cannot see (Gradescope, iClicker, paper exams) and what to improve. Free: no LLM.

    .venv/bin/python -m scripts.enter_grade --list                     # what the agent is missing, per course
    .venv/bin/python -m scripts.enter_grade CS101 Midterm --score 31 --out-of 40 --source Gradescope \
        --improve "LSP vs DIP; test doubles; REST status codes"
    .venv/bin/python -m scripts.enter_grade CS101 Midterm --improve "..."   # feedback only, no score yet

Entries go to data/user_inputs.json; re-entering the same component replaces the earlier entry.
The course's grade, floor/ceiling and "needed" numbers are recomputed straight away (free recheck).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from canvas_agent.user_inputs import InputStore, UserEntry, split_areas

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("course", nargs="?")
    ap.add_argument("component", nargs="?", help="syllabus component name, e.g. Midterm")
    ap.add_argument("--item", help="assessment name if different from the component (e.g. 'Quiz 3')")
    ap.add_argument("--score", type=float)
    ap.add_argument("--out-of", type=float)
    ap.add_argument("--source", help="where the grade is shown, e.g. Gradescope")
    ap.add_argument("--improve", default="", help="what you lost marks on / want to improve; separate with ; or new lines")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list or not args.course:
        for f in sorted((DATA / "syllabi").glob("*.facts.json")):
            reqs = json.loads(f.read_text()).get("input_requests", [])
            for r in reqs:
                print(f"{r['course']}: {r['component']} ({r['weight_pct']:g}%) - {r['reason']}"
                      + (f" Likely in {r['likely_source']}." if r.get("likely_source") else ""))
        return
    facts = json.loads((DATA / "syllabi" / f"{args.course}.facts.json").read_text())
    names = [c["component"] for c in facts["component_checks"]]
    match = next((n for n in names if n.lower() == (args.component or "").lower()), None)
    if not match:
        raise SystemExit(f"Unknown component {args.component!r}. Components: {names}")
    if (args.score is None) != (args.out_of is None):
        raise SystemExit("Give both --score and --out-of (or neither for feedback only).")
    entry = UserEntry(course=args.course, component=match, item=args.item or match, score=args.score,
                      out_of=args.out_of, source=args.source, improvement_areas=split_areas(args.improve))
    InputStore(DATA / "user_inputs.json").add(entry)
    print(f"Saved: {args.course} {match}" + (f" {args.score:g}/{args.out_of:g}" if args.score is not None else "")
          + (f"; improvement areas: {entry.improvement_areas}" if entry.improvement_areas else ""))

    from scripts.syllabus_flow import recheck  # free: recompute grade with the new entry

    recheck(args.course, DATA / "syllabi" / f"{args.course}.pdf")
    g = json.loads((DATA / "syllabi" / f"{args.course}.facts.json").read_text())["syllabus_view_grade"]
    print(f"{args.course} grade now {g['current_pct']}% over {g['weight_determined']}% graded "
          f"(possible finish {g['floor_pct']}-{g['ceiling_pct']}%)")


if __name__ == "__main__":
    main()
