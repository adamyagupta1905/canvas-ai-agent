# Architecture decisions

Short records of choices that shape the system. Newest at the bottom.

## ADR-001: Stack and boundaries
- **Date:** 2026-11-23
- **Decision:** Python 3.13, Pydantic, pytest, Streamlit dashboard, Claude (`claude-opus-5-5`) via the
  Anthropic SDK behind `canvas_agent/llm.py`. LangGraph for orchestrating the specialist agents
  (ingest, notes, planner, quiz, tracker) once there is more than one LLM step. Every external system
  (Canvas, Notion, YouTube, LLM) sits behind a Protocol with an offline fake.
- **Alternatives:** One big tool-using agent loop; Managed Agents (hosted loop); LangChain wrappers.
- **Why:** The user is learning LangGraph and wants explicit, inspectable multi-agent flow. Most steps are
  predictable pipelines (fetch -> extract -> compute), so code-controlled graph edges beat an open-ended
  loop for cost, testability and evaluation. Protocols keep the suite key-free.
- **Revisit if:** Tasks become open-ended enough that a model-driven loop clearly wins, or hosting
  the loop ourselves becomes a burden.

## ADR-002: Structured outputs + grounding check for extraction
- **Date:** 2026-11-23
- **Decision:** LLM extraction uses `messages.parse(output_format=<Pydantic model>)`; each item carries a
  `source_quote` and Python drops any item whose quote is not found in the source text.
- **Alternatives:** Free-text answers parsed with regex; trusting the model output as-is.
- **Why:** Schema-valid output plus a cheap deterministic check stops invented exam dates from reaching the plan.
- **Revisit if:** Sources are PDFs/OCR where exact-substring matching is too strict (then use normalised/fuzzy match).
