# Use cases, mapped to five real course layouts (anonymised)

From a read-only survey of five real courses. This file records **structure only**: no grades, no course
identities and no course content. Real data stays in gitignored `data/`.

## The five course layouts

| Course | How Canvas is used | Grade setup in Canvas | Where the content is |
|---|---|---|---|
| A: software engineering | Grades only | Points total, no weights; each deliverable split into Auto / Manual / Total items | External course site (syllabus, schedule, slides, project spec), Gradescope, PrairieLearn |
| B: applied machine learning | Grades + homework files | Weighted; homework and exam marks not in Canvas (they are in PrairieLearn) | Course website, course book, notebooks, PrairieLearn, discussion forum |
| C: information systems | Everything | Weighted, seven components; drop lowest 3 in two components; 2 items left out of the grade | Modules per class: checklist, lecture pages, key terms, practice exercises **with solutions**, practice quizzes |
| D: law | Everything | Weighted, four components; drop lowest 2 in-class exercises | ~20 classes: prep pages, slide PDFs, textbook, **practice midterms and finals with answer keys**, oral-exam practice |
| E: business communication | Everything | Points total in Canvas; real weights only in the syllabus PDF | Weekly slide PDFs, assignment briefs, exemplars, rubrics |

## Use cases and which layouts need them

| # | Use case | What it does | Done by | Best sample layouts | Status |
|---|---|---|---|---|---|
| 1 | **Precise current grade** | Reproduces Canvas's own maths: weights, points, drop rules, left-out items, unreleased marks, extra credit | Python (`grades.py`) | All; checked against Canvas totals where shown | Built |
| 2 | **Finishing range + "what do I need"** | Floor / ceiling and the average needed on remaining work for a target | Python | B, C, D | Built |
| 3 | **Unified tracker** | Every item from 1-point polls to the final, with status, due date and % of final grade | Python | All | Built |
| 4 | **Sync-gap alerts** | Components where every score is 0, scores live in another tool, or a weighted component has no items | Python | B, C, D | Built |
| 5 | **Syllabus weights from PDF / website** | Pull the real weights when Canvas has none, then grade properly | LLM extracts, Python checks | A (website), E (PDF) | Built |
| 6 | **Topic mastery** | Score % per topic from graded evidence, then from quiz results | Python | C (exercise codes = topics), D (per-class exercises) | Basic |
| 7 | **Coverage** | How far through the course you are; sessions counted from Modules | Python | C, D | Basic |
| 8 | **Structured notes** | Notes per module/class from slides and prep pages, each point citing its source | LLM writes, Python checks citations | A, C, D, E | Next |
| 9 | **Quiz generation + marking** | Questions grounded in notes; Python marks and updates mastery | LLM writes, Python marks | C, D | Marking built |
| 10 | **Quiz quality eval** | Practice exams with answer keys as the labelled answer key | Python scoring | D, C | Next |
| 11 | **Exam study plan** | Day-by-day plan weighted by weak topics, study focus and grade at stake | Python schedules | B, C, D | Next |
| 12 | **External content ingestion** | Course websites (pages, tables, slide PDFs, OCR) + PrairieLearn | Python crawler / collector | A, B | Built |
| 13 | **Notion tracker** | Push the unified tracker to a Notion database | Adapter, opt-in writes | All | Later |
| 14 | **Oral / speech prep** | Practice prompts from rubrics and exemplars | LLM | D, E | Later |
| 15 | **Student-provided grades + study focus** | Ask for grades no connector can reach (Gradescope, iClicker); link improvement areas to course pages | Python | A, D, E | Built |
