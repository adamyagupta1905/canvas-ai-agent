# Progress

_Last updated: 2026-11-24_

> This file describes the system only. Student-specific state (real grades, entries, confirmations, open
> questions about specific courses) lives in `data/PROGRESS_PRIVATE.md`, which is gitignored. Read both.

## Current goal
v1: for any course a student connects (Canvas + PrairieLearn + course website + what they enter), show grades,
progress, notes, a plan and every deadline, deterministically, on a dashboard and through agents.

## Done (system)
- [x] Connectors: read-only Canvas client + ingest (syllabus discovery, redirect-tool site URLs), PrairieLearn in-browser
      collector + normaliser, polite course-website crawler with page-cited index (OCR once, cached), student input
- [x] Syllabus flow: text/OCR -> chunks -> hybrid BM25 -> two-pass extraction (components, per-component rules) ->
      Python validation; free `--recheck` replay of saved model output
- [x] Grade engine (Canvas-exact: weights, drops incl. never-drop items, omitted, unreleased, extra credit) + syllabus
      view, floor/ceiling, needed-for-target, pass-requirement checks, attendance check, exam-date hints
- [x] Five deterministic views (`course_view.py`): progress, notes (extracted outlines), merged deadlines, rule-based
      plan, all-courses overview
- [x] Streamlit dashboard: All courses + per-course tabs (Overview, Grades, Notes, Plan, Deadlines, Sources)
- [x] Agents: router + 4 agents (grades, planner, content, rules) over deterministic tools, ungrounded-number check
- [x] `scripts.export_dashboard` -> JSON contract for the Lovable "Semester Orbit" UI (built on fictional demo data)
- [x] Architecture diagrams (`docs/architecture-overview.svg`, `docs/architecture-agents.svg`) in README + ARCHITECTURE.md
- [x] Private GitHub repo `adamyagupta1905/canvas-ai-agent`; commits use the GitHub noreply address; CI green

## In progress
- [ ] Nothing mid-edit. Working tree clean at `349b036` before this handoff edit.

## Next up
1. **Exam dates the syllabus leaves open** ("exam period"): add them to `input_requests` so the student can enter the
   date and `course_view.plan` schedules review (today the plan skips exams without a date).
2. **Syllabus extraction eval** (`/llm-eval`): Canvas group weights are a free answer key for weighted courses;
   hand-label the points-only ones; report misses and rejects next to successes.
3. **Refresh path**: run `scripts.ingest_canvas` with a real read-only token; re-run the PrairieLearn collector and
   `scripts.crawl_site <CODE>`; then `scripts.syllabus_flow <CODE> --recheck` and `scripts.export_dashboard`.
4. **Lovable UI**: with credits, ask Lovable for a visual pass of the course pages; then sync to GitHub, run locally and
   swap in `data/dashboard.json` (never upload real data to Lovable).
5. **Quiz generation + eval** from practice exams that ship with answer keys (cited questions, Python marking).
6. Optional cited LLM summaries on top of the extracted note outlines.

## Blockers / open questions
- Lovable account is out of credits; its course pages (and possible type errors it mentioned) are unverified.
- `CanvasAPI` has only been tested against a fake server (needs the student's token in `.env`).
- One course site links a slide deck at a broken path (HTTP 503); a few image-only slides still leak OCR noise.

## Decisions (newest first)
- 2026-11-24 Outside UI builders get fictional data + the JSON contract only; real data stays local. (CLAUDE.md)
- 2026-11-24 Five deterministic views per course; four agents. (ADR-012)
- 2026-11-24 Ask the student for grades no connector can reach; label them in every calculation. (ADR-011)
- 2026-11-23 Course websites as a source: crawl once, index once, cite always. (ADR-010)
- 2026-11-23 PrairieLearn via in-browser collector, merged into Canvas gaps only. (ADR-009)
- 2026-11-23 Two-pass extraction + Python validation of every rule. (ADR-007)
- 2026-11-23 Grade engine reproduces Canvas rules, checked against Canvas totals. (ADR-003)

## Verification status
- Tests: 104 passing, `.venv/bin/python -m pytest -q`, 2026-11-24; CI green on `349b036`
- Grade engine: matches Canvas's own totals wherever Canvas shows them; other courses hand-checked
- Live: syllabus extraction (gemini-3.8-flash, 2 calls per course) reviewed by hand; agent smoke questions grounded
- Evals: no scored eval yet
- Not verified: ClaudeClient against the real API; Lovable course pages; snapshot freshness (pulls are manual)
