"""Ask the agents a question about your courses (paid: uses LLM_PROVIDER from .env, default gemini).

    .venv/bin/python -m scripts.ask "What do I need on the rest of LAW201 to finish with 80%?"
    .venv/bin/python -m scripts.ask --trace "Am I at risk on attendance in LAW201?"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from canvas_agent.agents import ask
from canvas_agent.llm import make_client
from canvas_agent.tools import Store, build_tools

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--trace", action="store_true", help="show the router decision and every tool call")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")  # keys are read by the SDKs; never printed
    r = ask(args.question, make_client(), build_tools(Store(ROOT / "data")))
    print(f"[{r.agent} agent, {r.steps} step(s), tools: {[t['tool'] for t in r.trace[1:]]}]\n")
    print(r.answer)
    if r.ungrounded_numbers:
        print(f"\nWARNING: numbers not found in any tool result: {r.ungrounded_numbers}")
    if args.trace:
        for t in r.trace:
            print(f"\n- {t['tool']} {t['args']}\n  {json.dumps(t['result'], default=str)[:600]}")


if __name__ == "__main__":
    main()
