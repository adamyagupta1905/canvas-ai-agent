"""Course progress dashboard.

    .venv/bin/streamlit run app.py

Real mode: reads data/canvas_snapshot.json + data/syllabi/<COURSE>.facts.json (local, gitignored).
Demo mode: falls back to the synthetic fixture course when there is no real data.
Every number on this page is computed by Python (canvas_agent.grades / component_check), never by the LLM.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from canvas_agent.grades import grade_summary, tracker

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SNAP = DATA / "canvas_snapshot.json"
SYL = DATA / "syllabi"

st.set_page_config(page_title="Course progress", layout="wide")


def load_courses() -> tuple[dict, bool]:
    if SNAP.exists():
        from canvas_agent.tools import Store

        return Store(DATA).courses(), True  # Canvas + PrairieLearn merged where Canvas has gaps
    from canvas_agent.sources import FixtureSource

    src = FixtureSource(ROOT / "tests" / "fixtures" / "courses")
    items = [[a.name, a.due_at.isoformat() if a.due_at else None, a.points_possible, a.state.value, a.score, False]
             for a in src.list_assignments("ECON101")]
    return {"ECON101": {"name": "Demo course (synthetic)", "weighted": False,
                        "groups": [{"name": "Assignments", "weight": 0, "rules": {}, "items": items}]}}, False


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.2f}%"


courses, real = load_courses()
now = datetime.now(timezone.utc)
codes = list(courses)
wanted = st.query_params.get("course")  # e.g. ?course=LAW201, so a course view can be bookmarked
code = st.sidebar.selectbox("Course", codes, index=codes.index(wanted) if wanted in codes else 0,
                            format_func=lambda c: f"{c}: {courses[c]['name']}")
st.query_params["course"] = code
course = courses[code]
facts_path = SYL / f"{code}.facts.json"
facts = json.loads(facts_path.read_text()) if facts_path.exists() else None
if not real:
    st.sidebar.info("Demo mode: synthetic data. Real data appears once data/canvas_snapshot.json exists.")

st.title(f"{code}: {course['name']}")
canvas_view = grade_summary(code, course)
syl_view = facts.get("syllabus_view_grade") if facts else None

# ---- headline -----------------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
if syl_view:
    c1.metric("Current grade (syllabus rules)", pct(syl_view["current_pct"]),
              help="Syllabus weights and rules applied to your released Canvas marks.")
    c2.metric("Canvas's own calculation", pct(canvas_view.current_pct),
              help="Canvas weights/points as configured. Differs when Canvas is not set up like the syllabus.")
    c3.metric("Weight already graded", pct(syl_view["weight_determined"]))
    c4.metric("Possible finish", f"{syl_view['floor_pct']:.1f}% – {syl_view['ceiling_pct']:.1f}%",
              help="Worst case: everything remaining scores 0. Best case: everything remaining scores 100%.")
else:
    src = " + ".join(["Canvas"] + [x for x in course.get("sources", []) if x != "Canvas"])
    c1.metric(f"Current grade ({src} data)", pct(canvas_view.current_pct),
              help="Canvas weights; PrairieLearn scores fill items Canvas has not released yet.")
    c2.metric("Floor / ceiling", f"{pct(canvas_view.floor_pct)} / {pct(canvas_view.ceiling_pct)}")
    st.info("No syllabus extraction for this course yet: grade uses Canvas's weights only.")
    if course.get("canvas_totals_canvas_only"):
        ct = course["canvas_totals_canvas_only"]
        st.caption(f"Canvas's own total covers Canvas data only ({ct['current']}%); the engine matches it on that data. "
                   "The grade above also includes PrairieLearn scores, so it differs by design.")

if facts and facts.get("needed"):
    st.caption("Average needed on everything still to be graded: "
               + " · ".join(f"{t}% → **{v:.1f}%**" + (" (out of reach)" if v > 100 else "")
                            for t, v in facts["needed"].items() if v is not None))

# ---- warnings -----------------------------------------------------------------------------------
warnings = [w for w in canvas_view.warnings
            if not (syl_view and "syllabus weights are needed" in w)]  # syllabus weights found: points-total caveat is moot
att = facts.get("attendance_check") if facts else None
warnings = list((facts or {}).get("past_exams_without_score", [])) + warnings
for w in warnings:
    st.warning(w)
if att:
    n = len(att["possible_absences"])
    limits = " · ".join(f"more than {t}% = more than {k} of {att['sessions_total']} classes"
                        for t, k in att.get("allowed_absences", {}).items())
    head = ("Attendance (confirmed)" if att.get("status") == "confirmed" else "Attendance (possible, unverified)")
    body = (f"**{head}: {n} absences out of {att['sessions_total']} classes ({att['possible_absence_pct']}%).**  \n"
            f"Syllabus limits: {limits}.  \n")
    if att.get("sessions_remaining") is not None:
        body += f"Classes still to come: {att['sessions_remaining']}.  \n"
    body += "Absences: " + ", ".join(re.sub(r"\s*in-class exercise", "", a, flags=re.I) for a in att["possible_absences"])
    body += f"  \n_{att['caveat']}_"
    (st.error if att.get("exceeds") else st.info)(body)
    if att.get("exceeds"):
        with st.expander("What the syllabus says"):
            for q in att["quotes"]:
                st.markdown(f"> {q}")

# ---- needs your input (Gradescope, iClicker, paper exams: things no connector can read) ----------
if real and facts:
    from canvas_agent.user_inputs import InputStore, UserEntry, split_areas

    istore = InputStore(DATA / "user_inputs.json")
    mine = istore.for_course(code)
    reqs = facts.get("input_requests", [])
    if reqs or mine:
        st.subheader("Needs your input")
    for r in reqs:
        with st.form(f"in-{code}-{r['component']}"):
            st.markdown(f"**{r['component']} ({r['weight_pct']:g}%)**: {r['reason']}"
                        + (f" Likely in **{r['likely_source']}**." if r.get("likely_source") else ""))
            c1, c2, c3 = st.columns([1, 1, 2])
            score = c1.number_input("Your score", min_value=0.0, value=None, step=0.5, key=f"s-{code}-{r['component']}")
            out_of = c2.number_input("Out of", min_value=0.0, value=None, step=0.5, key=f"o-{code}-{r['component']}")
            source = c3.text_input("Where you saw it", value=r.get("likely_source") or "", key=f"src-{code}-{r['component']}")
            improve = st.text_area("Scope for improvement: what did you lose marks on, or want to get better at? "
                                   "(separate with ; or new lines)", key=f"imp-{code}-{r['component']}")
            if st.form_submit_button("Save"):
                if (score is None) != (out_of is None) or (out_of is not None and out_of <= 0):
                    st.error("Enter both a score and a positive 'out of', or leave both empty to save feedback only.")
                else:
                    try:
                        istore.add(UserEntry(course=code, component=r["component"], item=r["component"], score=score,
                                             out_of=out_of, source=source or None, improvement_areas=split_areas(improve)))
                        from scripts.syllabus_flow import recheck  # free: recompute with your entry

                        recheck(code, SYL / f"{code}.pdf")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
    if mine:
        st.caption("What you've told me (used in the grade above and in your study focus):")
        st.dataframe(pd.DataFrame([{"Component": e.component, "Score": f"{e.score:g}/{e.out_of:g}" if e.score is not None else "-",
                                    "Source": e.source or "-", "Improve": "; ".join(e.improvement_areas)} for e in mine]),
                     hide_index=True, use_container_width=True)
        from canvas_agent.user_inputs import study_focus

        focus = study_focus(mine, DATA / "sites" / code)
        if focus:
            with st.expander("Study focus from your feedback"):
                for f in focus:
                    links = " · ".join(f"[{m['title']} p.{m['page']}]({m['url']})" for m in f["materials"]) or "no matching course pages found"
                    st.markdown(f"- **{f['area']}** ({f['component']}): {links}")

# ---- components ---------------------------------------------------------------------------------
if facts and facts.get("component_checks"):
    st.subheader("Components")
    checks = facts["component_checks"]
    table = pd.DataFrame([{
        "Component": c["component"], "Weight %": c["syllabus_weight"],
        "Canvas setup": ", ".join(c["canvas_groups"]) or "-",
        "Graded / expected": f"{c['scored']} / {c['expected_items'] if c['expected_items'] is not None else c['items_in_canvas']}",
        "Drops (syllabus / Canvas)": f"{c['drop_syllabus'] if c['drop_syllabus'] is not None else '-'} / {c['drop_canvas'] if c['drop_canvas'] is not None else '-'}",
        "Average now": c["avg_syllabus_rules"],
        "Adds to grade": round(c["syllabus_weight"] * c["avg_syllabus_rules"] / 100, 2) if c["avg_syllabus_rules"] is not None else None,
        "Flags": len(c["flags"]),
    } for c in checks])
    st.dataframe(table, hide_index=True, use_container_width=True)

    for c in checks:
        with st.expander(f"{c['component']} ({c['syllabus_weight']}%) · average {pct(c['avg_syllabus_rules'])}"):
            for f in c["flags"]:
                if not f.startswith("Expected item count"):  # shown as the caption below
                    st.markdown(f"- ⚠️ {f}")
            if c.get("expected_source"):
                st.caption(f"Expected count: {c['expected_items']} ({c['expected_source']})")
            rows = []
            for i in c["items"]:
                status = ("dropped" if i["dropped_syllabus"] else "counted") if i["score"] is not None else "not graded yet"
                if i["pct"] is None:
                    label = "not graded"
                elif status == "dropped":
                    label = f"{i['pct']:.0f}% (dropped)"
                else:
                    label = f"{i['pct']:.0f}%"
                rows.append({"Item": i["name"], "Score %": i["pct"] if i["pct"] is not None else 0, "Status": status,
                             "Score": i["score"], "Points": i["points"], "Label": label})
            if c.get("sessions_total"):  # one bar per class, including classes with no item and future classes
                present = {int(m.group(1)) for r in rows if (m := re.search(r"class\D{0,3}(\d{1,2})", r["Item"], re.I))}
                for n in range(1, c["sessions_total"] + 1):
                    if n not in present:
                        rows.append({"Item": f"Class {n:02d} (no exercise)", "Score %": 0, "Status": "no item for this class",
                                     "Score": None, "Points": None, "Label": "none"})
                rows.sort(key=lambda r: int(m.group(1)) if (m := re.search(r"class\D{0,3}(\d{1,2})", r["Item"], re.I)) else 99)
            if rows:
                df = pd.DataFrame(rows)
                df["Short"] = [f"C{int(m.group(1)):02d}" if (m := re.search(r"class\D{0,3}(\d{1,2})", n, re.I)) else n[:18]
                               for n in df["Item"]]
                chart = alt.Chart(df).mark_bar().encode(
                    x=alt.X("Short:N", sort=None, title=None, axis=alt.Axis(labelAngle=-45, labelLimit=160)),
                    y=alt.Y("Score %:Q", scale=alt.Scale(domain=[0, 100])),
                    color=alt.Color("Status:N", scale=alt.Scale(
                        domain=["counted", "dropped", "not graded yet", "no item for this class"],
                        range=["#2f6fdf", "#b8b8b8", "#f0c36d", "#e6e6e6"])),
                    tooltip=["Item", "Score", "Points", "Score %", "Status"],
                )
                labels = alt.Chart(df).mark_text(angle=270, align="left", dx=4, fontSize=10).encode(
                    x=alt.X("Short:N", sort=None), y=alt.Y("Score %:Q"), text="Label:N")
                st.altair_chart((chart + labels).properties(height=280), use_container_width=True)
            if c["rules"]:
                st.markdown("**Rules from the syllabus**")
                for r in c["rules"]:
                    st.markdown(f"- {r}")

    pending = [m for m in facts.get("item_mapping", []) if m["confidence"] in ("low", "ambiguous", "unmapped")]
    if pending:
        st.subheader("Needs your confirmation")
        st.caption("These Canvas items are not counted in the syllabus-rules grade until confirmed in "
                   "data/item_overrides.json (map to a component name, or null to leave out).")
        st.dataframe(pd.DataFrame([{"Canvas item": m["item"], "Best guess": m["component"] or "-",
                                    "Confidence": m["confidence"]} for m in pending]), hide_index=True)

# ---- PrairieLearn -------------------------------------------------------------------------------
if real and (DATA / "prairielearn_snapshot.json").exists():
    from canvas_agent.prairielearn import summarise
    from canvas_agent.tools import Store

    store = Store(DATA)
    pl = [x for x in store.pl() if x.course == code]
    if pl:
        st.subheader("PrairieLearn")
        summ = summarise(store.pl(), code, now)
        notes = store.merge_notes(code)
        if notes:
            st.caption("Merged into the grade above (Canvas is never overwritten):")
            st.dataframe(pd.DataFrame([{"Action": n.action, "Canvas": n.canvas_item or f"[{n.canvas_group}]",
                                        "PrairieLearn": n.pl_assessment, "Detail": n.detail} for n in notes]),
                         hide_index=True, use_container_width=True)
        for d in summ["credit_deadlines"]:
            when = datetime.fromisoformat(d["deadline"])
            hours = (when - now).total_seconds() / 3600
            msg = f"**{d['label']}: {d['title']}**: {d['credit_pct']:g}% credit until {when:%a %b %d, %I:%M %p} ({hours:.0f} h left); status: {d['status'].replace('_', ' ')}"
            (st.warning if hours < 48 and d["status"] in ("open_not_done", "not_started") else st.info)(msg)
        if summ["awaiting_grade"]:
            st.caption("Submitted, waiting for manual grading (shown as 0% on PrairieLearn, but not a zero): "
                       + ", ".join(f"{x['label']} ({x['pending_points']:g} pts pending)" for x in summ["awaiting_grade"]))
        st.dataframe(pd.DataFrame([{"Label": x.label, "Title": x.title, "Set": x.set, "Kind": x.kind,
                                    "Status": x.status.replace("_", " "), "Points": f"{x.earned:g}/{x.possible:g}" if x.possible else "",
                                    "Score %": x.pct, "Credit deadline": x.deadline.strftime("%b %d %H:%M") if x.deadline else ""}
                                   for x in pl]), hide_index=True, use_container_width=True, height=300)

# ---- course website -----------------------------------------------------------------------------
site_dir = DATA / "sites" / code
if real and (site_dir / "manifest.json").exists():
    from canvas_agent.website import find_doc, load_manifest, schedule_rows, search

    man = load_manifest(site_dir)
    st.subheader("Course website")
    kinds = {}
    for d in man.docs:
        kinds[d.kind] = kinds.get(d.kind, 0) + 1
    st.caption(f"{man.start_urls[0]} · crawled {man.crawled_at[:16].replace('T', ' ')} UTC · "
               + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())))
    sched = find_doc(site_dir, "schedule")
    if sched:
        rows = schedule_rows(sched)
        if rows:
            with st.expander(f"Schedule ({len(rows)} rows)", expanded=False):
                st.dataframe(pd.DataFrame(rows).fillna(""), hide_index=True, use_container_width=True)
    lectures = sorted((d for d in man.docs if d.kind == "lecture"), key=lambda d: d.url)
    if lectures:
        with st.expander(f"Lecture slides ({len(lectures)})"):
            for d in lectures:
                st.markdown(f"- [{d.title}]({d.url})")
    q = st.text_input("Search the course website (slides, textbook, project specs)", key=f"search-{code}")
    if q:
        for h in search(site_dir, q, k=5):
            snippet = re.sub(r"(?m)^#+\s*", "", h["snippet"]).replace("\n", " ")[:300]  # no giant headings
            st.markdown(f"**{h['title']}** · {h['kind']} · page {h['page']} · [open]({h['url']})")
            st.caption(snippet + "…")

# ---- exams + tracker ----------------------------------------------------------------------------
left, right = st.columns([1, 2])
with left:
    st.subheader("Exams")
    if facts and facts["facts"]["exams"]:
        for e in facts["facts"]["exams"]:
            st.markdown(f"- **{e['title']}**: {e['exam_date'] or 'date not stated'}  \n  _{(e['when_text'] or '').strip()[:120]}_")
    else:
        st.caption("No exam facts extracted yet.")
    for h in (facts or {}).get("exam_date_hints", []):
        st.info(f"Possible {h['kind']} date found by Python in {h['source']}: **{h['found_date']}**  \n_\"{h['quote']}\"_  \n"
                "Not confirmed by the extraction; check it.")
with right:
    st.subheader("Everything, from small to major")
    rows = tracker(code, course, now)
    df = pd.DataFrame([{"Item": r.name, "Component": r.group, "Due": r.due.strftime("%b %d") if r.due else "",
                        "Status": r.status, "Score %": r.pct, "% of final grade": r.weight_of_final, "Size": r.size}
                       for r in rows])
    status_filter = st.multiselect("Status", sorted(df["Status"].unique()), default=sorted(df["Status"].unique()))
    st.dataframe(df[df["Status"].isin(status_filter)], hide_index=True, use_container_width=True, height=320)

st.caption("Numbers: Python grade engine (matches Canvas's own totals where Canvas shows them). "
           "Rules: extracted from the syllabus with an exact quote for every rule.")
