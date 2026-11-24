# Progress

_Last updated: 2026-11-24 (views + architecture)_

> Personal course results, grades and confirmations are kept out of git. The detailed, student-specific
> version of this file lives in `data/PROGRESS_PRIVATE.md` (gitignored). This file describes the system only.

## Current goal
Ship v1: from a student's courses (Canvas + PrairieLearn + course websites + what the student tells us), produce
precise grades, a unified tracker, notes, quizzes and exam plans. Use-case map: `docs/USE_CASES.md`.

## Done
- [x] Scaffold: package, LLM adapter, models, fixture source, syllabus extraction + grounding check, CI
- [x] Read-only Canvas survey via a signed-in browser session -> `data/canvas_snapshot.json` (gitignored; drop rules,
      omitted items, Canvas totals; module titles containing passwords are never stored)
- [x] `grades.py`: Canvas-exact grade engine (weights, points, drop-lowest best-%, omitted, unreleased, extra credit),
      Canvas "final" view, floor/ceiling, needed-for-target by bisection, unified tracker, topic mastery, sync-gap warnings.
      Matches Canvas's own totals to 2 dp wherever Canvas shows them.
- [x] `GeminiClient` + `make_client()` (`LLM_PROVIDER`, default gemini); Anthropic behind the same Protocol
- [x] Standard syllabus flow (`docs/SYLLABUS_FLOW.md`): text layer + Tesseract OCR fallback, page-cited chunks,
      hybrid BM25 retrieval (percent-dense chunks + next-chunk expansion), grounded extraction, Python checks,
      Canvas reconciliation (match / split / merged / only-in-one); free dry run and free `--recheck` replay
- [x] Rules pass + per-component validation (`component_check.py`): session counts from Canvas Modules, numbering gaps
      explained, drops/aggregation compared, attendance thresholds, exam-date hints, past exams without a score,
      syllabus-rules grade + needed
- [x] Automatic ingestion path: `CanvasAPI` (read-only token) + `ingest.py` find/download the syllabus
      (Syllabus-page PDF -> Modules PDF -> Syllabus HTML -> ask for a file); `scripts/ingest_canvas.py`
- [x] PrairieLearn connector: in-browser collector (GET only, no classmates' data) + normaliser (pending manual grading,
      hidden scores, redo/practice, credit deadlines in course time zone); merge fills Canvas gaps only
- [x] Course-website pipeline: polite same-site crawler (scopes, robots, caps, ETags), tables kept as rows, slide PDFs
      with OCR, page-cited index + search; syllabus flow can read the site; content agent cites URL + page
- [x] Student input for unreachable data (Gradescope, iClicker, paper exams): input requests, dashboard form +
      `scripts.enter_grade`, entries labelled "(entered by you)" in the grade, improvement areas -> study focus
- [x] Agents: router + grades / attendance / deadlines / syllabus / content agents over deterministic tools,
      ungrounded-number check; `scripts/ask.py`
- [x] Five deterministic views per course (`course_view.py`): progress, notes (extracted outlines), merged deadlines,
      rule-based plan, all-courses overview. Dashboard: All courses page + tabs Overview · Grades · Notes · Plan ·
      Deadlines · Sources. Agents simplified to 4 (grades, planner, content, rules) with the views as tools
- [x] `docs/ARCHITECTURE.md`: data-flow and agents/tools diagrams
- [x] 95 offline tests (fake Canvas server asserts GET-only; fake website; real Tesseract OCR test; CI installs Tesseract)

## In progress
- [ ] Nothing mid-edit.

## Next up
1. Resolve course-website URLs that sit behind Canvas external-tool links, then crawl them and run the syllabus flow.
2. **Syllabus extraction eval**: where Canvas has group weights they are a free answer key; hand-label the rest.
3. Repeatable pulls: Canvas token for `scripts.ingest_canvas`; re-run the PrairieLearn collector to refresh.
4. **Quiz-generation eval** from practice exams that come with answer keys.
5. Notes + quiz generation (Gemini) once a small paid run is approved.
6. Exam study plans driven by weak topics, study focus and the share of grade at stake.

## Blockers / open questions
- `CanvasAPI` not yet run against real Canvas (needs the student's token in `.env`); tested only against a fake server.
- `gemini-2.5-flash` is retired for new users; default is `gemini-3.8-flash` (verified by a live call).

## Decisions (newest first)
- 2026-11-24 Ask the student for grades no connector can reach; label them in every calculation. (ADR-011)
- 2026-11-23 Course websites as a source: crawl once, index once, cite always. (ADR-010)
- 2026-11-23 PrairieLearn via in-browser collector, merged into Canvas gaps only. (ADR-009)
- 2026-11-23 Specialist agents on one key, tools for everything deterministic. (ADR-008)
- 2026-11-23 Two-pass extraction (components, then per-component rules) + Python validation. (ADR-007)
- 2026-11-23 Grade engine reproduces Canvas rules and is checked against Canvas totals. (ADR-003)
- 2026-11-23 Real course data only in gitignored `data/`; tests stay synthetic.

## Verification status
- Tests: 95 passing, `.venv/bin/python -m pytest -q`, 2026-11-24
- Grade engine: matches Canvas's own totals on every course where Canvas shows them; others hand-checked
- Evals: live extraction runs reviewed by hand against Canvas and the source documents; no scored eval yet
- Not verified: ClaudeClient against the real API; snapshot freshness (pulls are manual)
