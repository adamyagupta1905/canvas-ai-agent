"""Crawl a course website into data/sites/<COURSE>/ (pages as markdown, PDFs, manifest). Free: no LLM.

    .venv/bin/python -m scripts.crawl_site CS101 https://course.example.edu/cs101/ --scope /cs101/ --scope /textbook/
    .venv/bin/python -m scripts.crawl_site CS101          # re-crawl with the saved settings (only changed files are re-downloaded)

Settings are remembered in data/sites.json.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from canvas_agent.website import SiteConfig, build_index, crawl

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / "data" / "sites.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("course")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--scope", action="append", default=[])
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--max-files", type=int, default=80)
    args = ap.parse_args()
    reg = json.loads(REG.read_text()) if REG.exists() else {}
    if args.url:
        reg[args.course] = {"start_urls": [args.url], "scopes": args.scope}
        REG.parent.mkdir(parents=True, exist_ok=True)
        REG.write_text(json.dumps(reg, indent=1))
    if args.course not in reg:
        ap.error("first run needs a URL")
    cfg = SiteConfig(code=args.course, start_urls=reg[args.course]["start_urls"], scopes=reg[args.course]["scopes"],
                     max_pages=args.max_pages, max_files=args.max_files)
    m = crawl(cfg, ROOT / "data" / "sites" / args.course)
    kinds = Counter(d.kind for d in m.docs)
    print(f"{args.course}: {len(m.docs)} documents {dict(kinds)}, {sum(d.bytes for d in m.docs) // 1024} KB; "
          f"{len(m.skipped)} skipped; {len(m.external_links)} outbound links recorded")
    for s in m.skipped[:10]:
        print("  skipped:", s)
    chunks = build_index(ROOT / "data" / "sites" / args.course, args.course)
    print(f"  index: {len(chunks)} page-cited chunks (text layer + OCR done once)")


if __name__ == "__main__":
    main()
