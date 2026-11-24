"""Streamlit dashboard (demo mode: synthetic fixture course, no key needed).

    .venv/bin/streamlit run app.py
"""

from datetime import datetime
from pathlib import Path

import streamlit as st

from canvas_agent.progress import course_progress
from canvas_agent.sources import FixtureSource

source = FixtureSource(Path(__file__).parent / "tests" / "fixtures" / "courses")
course = st.sidebar.selectbox("Course", ["ECON101"])
now = datetime(2026, 10, 5)  # fixed demo date so the synthetic data looks sensible

st.title(f"{course} progress (demo data)")
p = course_progress(course, source.list_assignments(course), now)
c1, c2, c3 = st.columns(3)
c1.metric("Completed", f"{p.completed}/{p.total}")
c2.metric("Completion", "n/a" if p.completion_pct is None else f"{p.completion_pct}%")
c3.metric("Overdue", len(p.overdue))
st.write("**Next due:**", p.next_due or "nothing dated")
if p.overdue:
    st.warning("Overdue: " + ", ".join(p.overdue))
st.caption("Notes, study plan and quizzes arrive in later milestones. See PROGRESS.md.")
