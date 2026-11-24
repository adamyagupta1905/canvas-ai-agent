# Canvas Study Agent

An AI study assistant for students: point it at a Canvas course and it builds structured lecture
notes, finds midterm/final dates in the syllabus and plans revision, generates and marks quizzes from
the lecture content, and tracks progress on a per-course dashboard.

**Design rule:** the LLM reads, extracts and writes; Python does every calculation. Extracted facts
must quote their source or they are dropped.

## Results
| Feature | Baseline | Pipeline | Cases | Notes |
|---|---|---|---|---|
| Syllabus exam extraction | 2/2 (regex) | not run | 1 | answer key still too easy |

## Quick start (no API key needed)
```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # offline tests
.venv/bin/streamlit run app.py         # demo dashboard on synthetic data
```
To use real services: `cp .env.example .env` and fill it in locally. Never commit `.env`.

## How it works
Every course gets the same dashboard tabs (Overview, Grades, Notes, Plan, Deadlines, Sources), built
deterministically from Canvas, PrairieLearn, the course website and what you enter. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data flow and the agents and their tools.

## Status
Early scaffold. See [PROGRESS.md](PROGRESS.md) for the plan and [docs/DECISIONS.md](docs/DECISIONS.md) for design choices.
