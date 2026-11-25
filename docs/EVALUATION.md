# Evaluation

Measured on 2026-11-24. Real-course results are reported in aggregate only (no course, student or grade is named);
the per-course detail stays in the gitignored `data/` folder.

## 1. Grade engine vs Canvas (deterministic, no LLM)

| Check | Result |
|---|---|
| Courses where Canvas shows its own total | 2 |
| Engine matches Canvas to 2 decimals on both the "current" and "final" views | **2 / 2** |
| Rules covered | group weights, points, drop-lowest (best-% choice), omitted and unreleased items, extra credit |

The other courses hide their totals, so their grades are hand-checked component by component (worked examples in
the tests, e.g. `tests/test_grades.py`, `tests/test_component_check.py`).

## 2. Syllabus extraction (LLM step, verified by Python)

Model: `gemini-3.8-flash`, 2 calls per course (components, then per-component rules). Sources: 3 syllabus PDFs and
2 syllabus web pages (5 real courses).

| Metric | Result |
|---|---|
| Courses whose grounded component weights sum to 100 | **5 / 5** |
| Components extracted (all with a verbatim quote) | 27 |
| Rules kept after Python validation | 46 |
| Items rejected by validation today | 1 (an item count the quote did not state: a correct rejection) |
| Component weights that agree with Canvas group weights (3 courses where Canvas has weights) | **16 / 16** (13 exact, 2 split across Canvas groups, 2 merged into one Canvas group) |
| Same metric on the first live pass, before Python matching fixes | 13 / 16 |

The model output did not change between 13/16 and 16/16. The three misses were in Python matching: an abbreviation
("HW" vs "Homework") and two syllabus components that Canvas merges into one group. Both are now handled and tested.

## 3. What failed (and what changed)

Validation on real data exposed these; each fix has a regression test.

| Failure | Fix |
|---|---|
| Keyword retrieval missed one course's grade table entirely | percent-dense chunks + next-chunk expansion |
| Class count not stated in the syllabus | Python counts sessions from Canvas Modules |
| First session count was wrong (modules omit later exercises) | a class counts if Canvas *or* its module has its item |
| "Late = zero" rule rejected because the model invented a number | keep the rule, drop the invented number |
| Singular / active-voice drop rules rejected ("We will drop your lowest homework grade") | wording handled; "excluding Homework 5" protected from drops |
| An attendance rule read as a 20% grade requirement | attendance wording excluded from pass checks |
| An exam date missed by extraction | Python date scan flags the dated sentence with its quote |
| Equal-weight deliverables averaged by points | equal weighting applied as the syllabus states |
| Same deadline listed twice from two sources | merged by local date + matching key ("D4" = "DELIV4") |
| Time zones: course-page times treated as UTC | course-local time everywhere |

## 4. Agents (live smoke tests)

3 questions on real data (router + one agent, 3-4 Gemini calls each). All answers used only tool results; the
ungrounded-number check flagged nothing. This is a smoke test, not a scored evaluation.

## 5. Synthetic baseline (offline, in CI)

`evaluation/run_eval.py` compares a regex baseline with the LLM pipeline on `evaluation/answer_key.json`. The answer key
has 1 synthetic case and the baseline scores 2/2, so it only proves the harness runs.

## Caveats
- No held-out set: the same 5 syllabi were used to find and fix the failures above, so 16/16 is optimistic for
  unseen syllabi.
- Agent quality is smoke-tested, not scored.
- Next: a scored extraction eval on new syllabi (Canvas weights as the free answer key), via `/llm-eval`.
