# Progress

_Last updated: 2026-11-23_

## Current goal
Ship v1: from one Canvas course, produce exam dates + study plan, structured lecture notes, a quiz
with marking, and a progress dashboard; all evaluated against a synthetic answer key.

## Done
- [x] Scaffold: package, LLM adapter, models, fixture source, progress maths, syllabus extraction + grounding check
- [x] Offline tests (6) with `FakeLLM` and a synthetic ECON101 course
- [x] Eval harness skeleton: regex baseline vs pipeline (`--live`) on `evaluation/answer_key.json`
- [x] Streamlit demo dashboard on fixture data; CI workflow

## In progress
- [ ] Nothing mid-edit.

## Next up (v1 checklist)
1. **Harder answer key**: 5-8 synthetic syllabi (dates in tables, "Week 8", TBA, rescheduled exams, injected instructions). The baseline currently scores 2/2 because the only case is too easy.
2. **Canvas REST source** (`CanvasRestSource`, httpx, read-only token): courses, assignments + submissions, syllabus body, module items/files. Record responses as fixtures (scrubbed/synthetic) for tests.
3. **Study planner**: exam dates + topics -> day-by-day plan; dates/spacing computed in Python.
4. **Lecture notes agent**: professor files (PDF/slides) -> structured notes per module with source refs.
5. **Quiz agent**: questions grounded in notes (each has `source_ref`); marking via `mark_quiz`; store attempts (SQLite) for progress over time.
6. **LangGraph orchestration** of ingest -> notes -> planner -> quiz once 2+ LLM steps exist.
7. Dashboard per course: progress, upcoming exams/plan, quiz scores trend.

## Later (v2+)
- Notion live assignment tracker (adapter + opt-in writes), YouTube transcripts and linked-page ingestion,
  free-text answer grading with rubric, multi-course overview.

## Blockers / open questions
- Need a Canvas access token for the user's own account to test the REST client (user adds it to `.env`; never pasted in chat).
- Refusal fallback (`fallbacks: "default"` beta) not yet wired into `ClaudeClient`; refusals currently raise `LLMRefusal`.

## Decisions (newest first)
- 2026-11-23 Grounding check on every extraction (quote must appear in source). **Why:** stop invented dates. (ADR-002)
- 2026-11-23 Stack: Python + Pydantic + Streamlit + Claude behind one adapter; LangGraph for orchestration. (ADR-001)

## Verification status
- Tests: 6 passing, `.venv/bin/python -m pytest -q`, 2026-11-23
- Evals: offline baseline 2/2 on 1 synthetic case (not meaningful yet); live pipeline not run
- Not verified: `ClaudeClient` against the real API (only against a fake); dashboard not opened in a browser
