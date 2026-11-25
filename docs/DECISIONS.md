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

## ADR-003: Grade engine reproduces Canvas's gradebook rules
- **Date:** 2026-11-23
- **Decision:** `grades.py` follows Canvas exactly: omit_from_final items excluded; only released scores count
  ("current"), unreleased scored 0 for "final"; drop_lowest/drop_highest chosen to maximise the group %;
  0-point items with a score are extra credit; weighted groups with no countable points are left out and weights
  re-normalised. Floor/ceiling/needed use the same engine with remaining work filled at a uniform rate.
- **Alternatives:** Simple weighted average of item percentages; ignoring drop rules.
- **Why:** The user wants precision, and Canvas's own totals give a free correctness check. On the survey it
  matched Canvas to 2 dp on both courses that show totals (current and final views).
- **Revisit if:** A course uses never_drop rules, letter/pass-fail grading types, or syllabus rules kept off Canvas.

## ADR-004: Gemini as default provider, Anthropic kept behind the same interface
- **Date:** 2026-11-23
- **Decision:** `make_client()` returns `GeminiClient` (google-genai, structured JSON via `response_schema`)
  unless `LLM_PROVIDER=anthropic`. Both live in `llm.py` only.
- **Why:** The user has a Gemini key and wants room for Anthropic. One Protocol keeps everything else provider-free.
- **Revisit if:** Eval results show one provider is clearly better for extraction or quiz quality.

## ADR-005: Syllabus flow = text layer, OCR fallback, BM25 retrieval, grounded extraction
- **Date:** 2026-11-23
- **Decision:** Per-page pypdf text; pages under 50 chars are rendered with pypdfium2 and OCR'd by Tesseract.
  Page-bounded chunks with stable ids, BM25 multi-query retrieval per topic, LLM extraction from retrieved
  chunks only, then Python checks (quote in chunk, number in quote, weights sum to 100) and reconciliation with Canvas.
- **Alternatives:** Send the whole PDF to the model (native PDF input); embedding retrieval; cloud OCR.
- **Why:** Works offline and free up to the model call; every fact is page-cited; OCR only where needed;
  BM25 is deterministic and testable. pypdfium2 avoids a poppler system dependency.
- **Revisit if:** Syllabi are mostly scanned tables Tesseract misreads (try layout-aware OCR or the model's
  native PDF/vision input), or retrieval misses grading tables (add embeddings).

## ADR-006: Ingestion through a read-only Canvas token, browser session only for one-off surveys
- **Date:** 2026-11-23
- **Decision:** The app pulls Canvas data and syllabus files with `CanvasAPI` (personal access token, GET only).
  The signed-in browser session was used once for the initial survey.
- **Alternatives:** Keep reading through the browser session; ask the student to download files.
- **Why:** The browser route cannot move files (184-560 KB PDFs did not fit through the tool) and is not
  repeatable from code. A token is the standard, revocable, scriptable path and keeps data local.
- **Revisit if:** A school disables student tokens; then fall back to user-supplied files (`--file`).

## ADR-007: Two-pass extraction (components, then per-component rules) with Python validation of every rule
- **Date:** 2026-11-23
- **Decision:** Pass A extracts components/exams/policies; pass B gives the model each component's own retrieved
  excerpts and asks only for that component's rules (one call for all components). Python then checks each rule
  against Canvas (counts, gaps, drops, aggregation, completion zeros) and builds a syllabus-rules grade.
- **Alternatives:** One big extraction call; letting the model compute component averages.
- **Why:** Rules live in per-component paragraphs that a single grading-focused retrieval misses. Keeping maths and
  counting in Python made every edge case found in validation fixable without new model calls (`--recheck`).
- **Revisit if:** Syllabi with many components make pass B too long (split it per component).

## ADR-008: Specialist agents on one key, tools for everything deterministic
- **Date:** 2026-11-23
- **Decision:** A router (structured output) sends each question to one of four agents (grades, attendance,
  deadlines, syllabus). Each agent runs a small loop: the model returns `AgentStep` (call one tool, or answer);
  Python runs the tool from `tools.py`. All agents share `make_client()`, so one key (Gemini now, Claude later).
  After answering, Python flags any number in the answer that appears in no tool result.
- **Alternatives:** Provider-native function calling; LangGraph supervisor graph; one generalist agent.
- **Why:** Structured-output steps work identically on Gemini and Claude and are testable with a scripted fake.
  Per-agent tool lists keep each agent narrow. LangGraph can wrap the same agents later if flows get branchy.
- **Revisit if:** Questions often need two agents (add a planner that chains them) or step counts grow (native tool calling).

## ADR-009: PrairieLearn via an in-browser collector + Python normaliser, merged into Canvas gaps only
- **Date:** 2026-11-23
- **Decision:** A small JS collector runs in the student's signed-in PrairieLearn tab (same-origin GETs), reads the
  assessments list and each assessment page, and returns compact JSON (no group members or join codes).
  `prairielearn.py` turns it into typed assessments (graded / redo / practice / tutorial; graded / awaiting grade /
  partially graded / score hidden / not started / not yet available) with credit deadlines in the course time zone,
  then fills only the Canvas items or empty weighted groups that have no released score.
- **Alternatives:** PrairieLearn API tokens (course-staff endpoints, not student data); scraping with the student's
  cookie from Python (holds a credential in code); manual export.
- **Why:** Students' own data is only on their pages; running in their session holds no credential and stays read-only.
  "0% + pending manual grading" and redo/practice copies are common, so a typed normaliser avoids false zeros and double counting.
- **Revisit if:** PrairieLearn adds student-scoped API tokens; then replace the collector with an HTTP client.

## ADR-010: Course websites as a first-class source (crawl once, index once, cite always)
- **Date:** 2026-11-23
- **Decision:** `website.py` crawls a public course site within path scopes (robots.txt, delay, caps, ETags), keeps
  main-content text with tables flattened to "a | b" rows, downloads linked PDFs, and builds a page-cited BM25 index
  (OCR done once at index time). The syllabus flow uses the site's syllabus page when there is no PDF; for courses
  whose Canvas is only a points mirror, the grade uses PrairieLearn's own groupings, Canvas zeros fill PL gaps, and
  closed-with-nothing-earned work counts as 0. A content agent answers "where/when was X covered" with URL + page.
- **Alternatives:** Headless browser crawling; embedding index; asking the student for files.
- **Why:** Some courses keep their syllabus, schedule, slide decks and project specs only on their site; static HTML makes
  a plain HTTP crawler enough, and BM25 keeps search free and offline.
- **Revisit if:** A site renders content with JavaScript only (add a headless fetch), or needs a login (out of scope).

## ADR-011: Ask the student for what no connector can reach
- **Date:** 2026-11-24
- **Decision:** Python derives input requests from gaps (a past exam with no score anywhere; a weighted component no
  connected system holds). The student gives the score + out of + source and free-text improvement areas (dashboard
  form or `scripts.enter_grade`). Scores enter the syllabus-rules grade as "(entered by you)" items, replacing a
  placeholder for that component; improvement areas are matched to course pages by search to form a study focus.
- **Alternatives:** Scraping Gradescope with the student's session; assuming 0 for missing marks; skipping the component.
- **Why:** Gradescope/iClicker hold grades behind logins we do not automate; asking is honest, and the floor/ceiling
  already shows the stakes. Feedback in the student's own words is the best signal for planning.
- **Revisit if:** A Gradescope export or API becomes available to students.

## ADR-012: Five deterministic views per course; four agents
- **Date:** 2026-11-24
- **Decision:** `course_view.py` builds progress, notes, deadlines, plan and an all-courses overview with no LLM:
  progress from schedule weeks / Modules classes / dated items; notes as extracted outlines (deck title, learning goals,
  slide titles; repeated headers and code removed; Canvas modules as fallback); deadlines merged across Canvas,
  PrairieLearn, website schedule and syllabus (same local day + matching key = one entry); plan by fixed rules (work
  block the day before each deadline, exam review 7/4/2/1 days before, weak areas first, then course units). The
  dashboard shows them as tabs; the agents call them as tools. Agents reduced from 5 to 4 (grades, planner, content, rules).
- **Alternatives:** LLM-written notes and plans; one generalist agent.
- **Why:** Deterministic views are free, instant, testable and never invent content. Fewer agents = simpler routing.
- **Revisit if:** Students need prose summaries of lectures (add an optional, cited LLM notes pass on top of the outline).

## ADR-013: Build the Orbit UI in the repo instead of Lovable
- **Date:** 2026-11-24
- **Decision:** A static page in `web/` (plain HTML/CSS/JS, no build step, no dependencies) draws
  `/dashboard.json`, which `scripts/serve_web.py` builds fresh on every load with `scripts/export_dashboard.export` and
  serves on 127.0.0.1 only. Grade bands, the average and the 14-day timeline are computed in the exporter, not in the
  page. Course text is inserted with `textContent` only, and a Content-Security-Policy blocks other origins.
- **Alternatives:** Keep iterating in Lovable (out of credits, its own invented data, real data could never go there);
  a React/Vite app (adds a Node toolchain to a Python repo).
- **Why:** It runs on real data locally without the data leaving the machine, ships with the repo and CI, and keeps the
  "Python does the maths" rule checkable: the page has no grade arithmetic.
- **Revisit if:** The UI needs editing (entering grades from the page) or hosting; then add POST routes behind the same
  `user_inputs` code with tests, or move to a framework.
