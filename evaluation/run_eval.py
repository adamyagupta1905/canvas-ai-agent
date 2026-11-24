"""Syllabus exam extraction: naive regex baseline vs LLM pipeline.

Offline (default): runs the baseline only. No key, no cost.
Live (--live): also runs the Claude pipeline. Costs money; run only when authorised.

    python -m evaluation.run_eval
    python -m evaluation.run_eval --live
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY = ROOT / "evaluation" / "answer_key.json"
RESULTS = ROOT / "evaluation" / "results"

DATE_RE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})")


def baseline(text: str) -> list[dict]:
    """Per line: if it mentions midterm/final, take the first date on that line (or null)."""
    out = []
    for line in text.splitlines():
        low = line.lower()
        for kind in ("midterm", "final"):
            if f"{kind} exam" in low and not low.startswith("grading"):
                m = DATE_RE.search(line)
                d = datetime.strptime(m.group(0), "%B %d, %Y").date().isoformat() if m else None
                out.append({"kind": kind, "exam_date": d})
    return out


def pipeline(text: str) -> list[dict]:
    from canvas_agent.llm import ClaudeClient
    from canvas_agent.syllabus import extract_exams

    res = extract_exams(ClaudeClient(), text)
    return [{"kind": e.kind, "exam_date": e.exam_date.isoformat() if e.exam_date else None} for e in res.exams]


def score(pred: list[dict], expected: list[dict]) -> dict:
    exp = {(e["kind"], e["exam_date"]) for e in expected}
    got = {(p["kind"], p["exam_date"]) for p in pred}
    return {"tp": len(exp & got), "fp": sorted(got - exp), "fn": sorted(exp - got)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also run the paid Claude pipeline")
    args = ap.parse_args()

    key = json.loads(KEY.read_text())
    systems = {"baseline": baseline} | ({"pipeline": pipeline} if args.live else {})
    report = {}
    for name, fn in systems.items():
        rows = []
        for case in key["cases"]:
            text = (ROOT / case["syllabus_path"]).read_text()
            rows.append({"id": case["id"], **score(fn(text), case["expected"])})
        tp = sum(r["tp"] for r in rows)
        total = sum(len(c["expected"]) for c in key["cases"])
        report[name] = {"recall": f"{tp}/{total}", "cases": rows}
        print(f"{name}: {tp}/{total} exact matches")
        for r in rows:
            if r["fp"] or r["fn"]:
                print(f"  FAIL {r['id']}: false+ {r['fp']} missed {r['fn']}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
