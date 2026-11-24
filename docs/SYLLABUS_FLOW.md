# Standard syllabus flow

## Step 0: getting the syllabus (no manual downloads)
`canvas_agent/ingest.py` + `scripts/ingest_canvas.py`. Given Canvas access (read-only token in `.env`), it
tries, in order, and records which one it used:
1. a PDF linked from the Canvas Syllabus page (downloaded via the API)
2. a PDF in Modules whose title mentions "syllabus" or "outline"
3. the Syllabus page text itself, when the syllabus is typed into Canvas (HTML -> one page)
4. nothing found -> ask the user for a file; `--file` sends a user-supplied PDF/text through the identical flow

```bash
.venv/bin/python -m scripts.ingest_canvas --term 2026            # all current courses, free dry run
.venv/bin/python -m scripts.ingest_canvas --course-ids 193001 --live  # paid extraction
.venv/bin/python -m scripts.ingest_canvas --file ~/Downloads/x.pdf --course LAW201
```
The same pull also refreshes `data/canvas_snapshot.json` (weights, drop rules, omitted/excused items,
Canvas totals when shown), so grades and syllabus facts always come from the same moment.

One pipeline for every course syllabus, whether it is a clean PDF, a scanned PDF, or a mix.
Code: `canvas_agent/documents.py` -> `retrieval.py` -> `syllabus_flow.py` -> `reconcile.py`.
Runner: `scripts/syllabus_flow.py` (dry run is free; `--live` calls the model).

```
 data/syllabi/<COURSE>.pdf
        |
 1. Text layer      pypdf, per page                                   [Python]
        |  page has < 50 chars?
 2. OCR fallback    render page (pypdfium2, 300 dpi) -> Tesseract     [Python, offline]
        |  every page tagged text_layer | ocr | empty
 3. Chunk           paragraphs packed to ~1200 chars, 200 overlap,
                    never across pages, ids like LAW201-p3-c2        [Python]
        |
 4. Retrieve        BM25 over chunks, 3-5 phrasings per topic:
                    grading / exams / policies -> top chunks;
                    grading also adds every chunk with 3+ percentages
                    and the chunk after each hit (split tables)       [Python, offline]
        |
 5. Extract         model sees ONLY retrieved chunks, returns
                    SyllabusFacts (components, exams, policies),
                    each with chunk_id + exact quote                  [LLM: Gemini or Claude]
        |
 6. Validate        quote must appear in the cited chunk;
                    a weight's number must appear in its quote;
                    weights must sum to 100 (reported, never fixed)   [Python]
        |
 5b. Rules pass     per component, its own retrieved excerpts (component-scoped
                    BM25 + where its weight was found) -> one call for all
                    components: item count, counted items, drops, points vs
                    equal weighting, missed/late work, completion marking,
                    pass requirements, bonus; each with chunk_id + quote      [LLM]
        |
 6b. Rule checks    quote in chunk (mis-cited chunk auto-corrected only if the
                    quote is verbatim elsewhere); counts/drops must state their
                    number; other invented numbers are removed, rule kept      [Python]
        |
 7. Reconcile       syllabus components vs Canvas groups:
                    match / weight_differs / split_in_canvas /
                    only_in_syllabus /
                    merged_in_canvas / only_in_canvas /
                    canvas_unweighted                                 [Python]
        |
 8. Component check per component: Canvas items, expected count (stated in the
                    syllabus, else counted from Canvas Modules "Class NN"),
                    numbering gaps explained, syllabus vs Canvas drops, points vs
                    equal-weight average, completion zeros, unconfirmed items  [Python]
        |
 9. Syllabus view   syllabus weights + rules + placeholders for promised items
                    not yet in Canvas -> grade engine: current, floor, ceiling,
                    needed for 60/70/80/90                            [Python]
        |
10. Cross-checks    attendance: zero-scored in-class items vs the syllabus's
                    "missing more than X%" thresholds; exam dates the extraction
                    missed, found by Python in syllabus text / module titles   [Python]
        |
 data/syllabi/<COURSE>.facts.json   (gitignored)  ->  app.py dashboard
```

## Rules this flow enforces
- The syllabus is data, not instructions (stated in the system prompt; excerpts wrapped in `<chunk>` tags).
- No fact without a quote from a real chunk. Rejected items are kept in `rejected` with the reason.
- The model does no arithmetic. Totals and comparisons are Python.
- A weight total other than 100 is a failed check for the student to see, never a silent plug.
- A name mismatch (e.g. "Oral exam" vs "Viva Voce") is flagged, not guessed.
- Counts (classes, items) are done by Python from the syllabus statement or Canvas Modules, never guessed by the model.
- For courses without Canvas weights, a Canvas item counts toward a syllabus component only when the match is
  confident or the student confirmed it in `data/item_overrides.json`; everything else is listed, not counted.
- `--recheck` replays the saved raw model output through the current checks for free, so check changes never
  need a new paid call.

## Why RAG for a short document
A syllabus is often 5-15 pages and would fit in one prompt. Retrieval still earns its place:
every fact gets a chunk id and page, the model reads only the parts that matter (less to misread,
cheaper), and the same index serves lecture notes and long course outlines later. BM25 is used first
because it is offline, free and deterministic; an embedding retriever can drop in behind `Retriever`.

## Evaluation plan
Where Canvas already holds group weights, those weights are a free answer key for the extraction step (component
weights). Courses whose Canvas setup is a points total need hand labels from the syllabus.

## Validation findings on real syllabi (anonymised)
Run on four real course syllabi (three PDFs, one course website) with gemini-3.8-flash, 2 calls per course. Course
identities and the student's results are kept out of git (`data/PROGRESS_PRIVATE.md`).

Retrieval: the first version (BM25 only) **missed one course's grade summary table** and split another's across two
chunks; adding percent-dense chunks + next-chunk expansion fixed both. One course's syllabus lists a single
"Midterm (25%)" that Canvas splits into two 12.5% groups (now `split_in_canvas`); another has two 15% projects that
Canvas merges into one 30% group (now `merged_in_canvas`).

Edge cases the runs exposed, and what changed:
| Found | Fix |
|---|---|
| No class count from the model; the PDF has no class-by-class schedule | Python counts sessions from Canvas Modules titles and explains classes with no assessed item as "by design" |
| First count used module listings only and undercounted (modules omit later exercises) | A class is covered if Canvas has its item OR its module lists one |
| Attendance thresholds: only one of two found from model quotes | Python reads "miss... more than X%" sentences straight from the syllabus text |
| "Late = zero for the team" rejected because the model invented value=100 | Rule kept, invented number dropped |
| Item-to-component matching too eager in a points-only course | Stricter confidence; low-confidence items listed for confirmation, not counted |
| An exam date missed by the extraction | Python date scan flags the dated sentence with its quote; module titles are a second source |
| "The lowest lab mark is dropped" rejected (no number written) | Singular "the lowest ... is dropped" counts as 1 |
| Missed work shown as "not done" instead of 0 | Closed-with-nothing-earned counts as 0; Canvas zeros fill PrairieLearn gaps |
| Equal-weighted deliverables averaged by points | Equal weighting applied as stated (every item rescaled to the same points) |
| Recheck duplicated rules on every run | Dedupe by component + kind + quote |
| "Nov. 16" split into two sentences | Abbreviated months protected before sentence split |
| Syllabus "Homework" did not match Canvas "HW" | Common course abbreviations expanded in name matching (HW, MT, ...) |
| "We will drop your lowest homework grade" rejected; "Homework 5 is excluded from the drop" ignored | Active-voice singular drops count as 1; never-drop items parsed from the quote and protected by the grade engine |
| Pass requirements left as text | Python checks them: weighted average of graded parts, average needed on the rest, status |
| An attendance rule ("missing more than 20% of classes") read as a 20% grade requirement | Attendance-worded rules are excluded from pass checks (attendance has its own check) |
