// Semester Orbit: a static view over /dashboard.json (built by scripts/export_dashboard.py).
// Rules: no arithmetic on grades here (Python computed every number, band and timeline entry); geometry only.
// Course text comes from syllabi and web pages, so it is always inserted as text, never as HTML.
"use strict";

const app = document.getElementById("app");
const TABS = ["overview", "grades", "notes", "plan", "deadlines", "sources"];
const SVG = "http://www.w3.org/2000/svg";
let DATA = null;
let homeView = "orbit";

// ---------- tiny DOM helpers (textContent only) ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const pct = (x) => (x === null || x === undefined ? "n/a" : `${Number(x).toFixed(2)}%`);
const bandClass = (b) => `band-${b || "none"}`;
const BAND_LABEL = { safe: "Safe", watch: "Watch", at_risk: "At risk" };
const BAND_COLOR = { safe: "#34d399", watch: "#fbbf24", at_risk: "#f87171" };
const fmtDay = (iso) => new Date(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
const fmtWhen = (iso) => new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const daysText = (d) => (d === null || d === undefined ? "" : d < 1 ? "today" : `in ${Number(d).toFixed(1)} days`);
const course = (code) => DATA.courses.find((c) => c.code === code);

// ---------- routing: #/  and  #/course/CODE/tab ----------
function route() {
  if (!DATA) return;
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  app.replaceChildren();
  if (parts[0] === "course" && course(parts[1])) renderCourse(course(parts[1]), TABS.includes(parts[2]) ? parts[2] : "overview");
  else renderHome();
  window.scrollTo(0, 0);
}

// ---------- home ----------
function renderHome() {
  document.title = "Semester Orbit";
  const sum = DATA.summary;
  app.append(
    h("header", { class: "top" },
      h("div", {},
        h("p", { class: "eyebrow" }, `Mission control · synced ${fmtWhen(DATA.generated_at)}`,
          DATA.demo ? h("span", { class: "demo-pill" }, "FICTIONAL DEMO") : null),
        h("h1", {}, "Your ", h("span", { class: "grad" }, "semester"), " in orbit")),
      h("div", { class: "right" },
        h("p", { class: "eyebrow" }, "Average current grade"),
        h("div", { class: "big" }, pct(sum.average_current_pct)),
        h("div", { class: "muted" }, `${sum.graded_courses} of ${sum.courses} courses have a course grade`))),
  );

  const toggle = h("div", { class: "toggle", role: "group", "aria-label": "View" },
    ...["orbit", "table"].map((v) => h("button", {
      "aria-pressed": String(homeView === v),
      onclick: () => { homeView = v; route(); },
    }, v === "orbit" ? "Orbit" : "Table")));
  app.append(h("section", { class: "panel" },
    h("div", { class: "panel-head" }, h("div", {}, h("p", { class: "eyebrow" }, "All courses"), h("h2", {}, homeView === "orbit" ? "Orbit" : "Table")), toggle),
    homeView === "orbit" ? orbit() : courseTable(),
    h("p", { class: "legend" },
      `Planet colour = grade band (Safe ≥ ${sum.bands.safe}%, Watch ${sum.bands.watch}–${sum.bands.safe}%, At risk < ${sum.bands.watch}%; grey = no course grade yet). Outer ring = course progress.`)));

  const asks = DATA.courses.flatMap((c) => c.input_requests.map((r) => ({ ...r, code: c.code })));
  const warns = DATA.courses.flatMap((c) => c.warnings.map((w) => ({ w, code: c.code })));
  app.append(h("div", { class: "grid2 row-gap" },
    h("section", { class: "panel" }, h("p", { class: "eyebrow" }, "Timeline river"), h("h2", {}, `Next ${sum.timeline_days} days`), river(DATA.timeline, true)),
    h("section", { class: "panel" }, h("p", { class: "eyebrow" }, "Missing marks"), h("h2", {}, "Needs your input"),
      asks.length ? asks.map((r) => h("div", { class: "card ask" },
        h("div", { class: "title" }, `${r.code} · ${r.component}`, r.weight_pct !== null && r.weight_pct !== undefined ? ` (${r.weight_pct}%)` : ""),
        h("div", { class: "muted" }, r.reason || ""),
        r.likely_source ? h("div", { class: "muted" }, `Likely in: ${r.likely_source}`) : null,
        h("code", {}, `python -m scripts.enter_grade ${r.code} "${r.component}" --score … --out-of …`)))
        : h("p", { class: "muted" }, "Nothing missing: every graded component has a source."))));

  if (warns.length) {
    app.append(h("section", { class: "panel" }, h("p", { class: "eyebrow" }, "Heads up"), h("h2", {}, "Warnings"),
      warns.map(({ w, code }) => h("div", { class: "card warn" }, h("span", { class: "title" }, `${code} · `), w))));
  }
}

function orbit() {
  const n = DATA.courses.length, flat = n <= 2, W = 640, H = flat ? 340 : 570, cx = W / 2, cy = flat ? 160 : 275, R = 190, pr = 44;
  const start = flat ? Math.PI : -Math.PI / 2; // one or two courses sit left and right of the core
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "orbit", role: "list", "aria-label": "Courses in orbit" });
  const defs = s("defs", {},
    s("radialGradient", { id: "core" }, s("stop", { offset: "0%", "stop-color": "#67e8f9" }), s("stop", { offset: "100%", "stop-color": "#a78bfa" })));
  svg.append(defs,
    s("circle", { cx, cy, r: R, fill: "none", stroke: "#2b3352", "stroke-dasharray": "3 6" }),
    s("circle", { cx, cy, r: R * 0.55, fill: "none", stroke: "#1f2640" }),
    s("circle", { cx, cy, r: 72, fill: "url(#core)", opacity: "0.92" }),
    s("text", { x: cx, y: cy - 14, fill: "#0b1020" }, "SEMESTER"),
    s("text", { x: cx, y: cy + 14, fill: "#0b1020", "font-size": "28" }, String(n)),
    s("text", { x: cx, y: cy + 34, fill: "#0b1020", "font-size": "11" }, n === 1 ? "course" : "courses"));
  DATA.courses.forEach((c, i) => {
    const a = start + (2 * Math.PI * i) / n;
    const x = cx + R * Math.cos(a), y = cy + R * Math.sin(a);
    const color = BAND_COLOR[c.band] || "#64748b";
    const ringR = pr + 9, circ = 2 * Math.PI * ringR;
    const prog = c.progress.course_progress_pct;
    const g = s("a", { href: `#/course/${encodeURIComponent(c.code)}/overview`, role: "listitem",
      "aria-label": `${c.code}: ${c.grade_is_course_grade ? pct(c.grade.current_pct) : "no course grade"}, ${BAND_LABEL[c.band] || "no band"}, course ${prog ?? "n/a"}% through` });
    g.append(
      s("circle", { cx: x, cy: y, r: ringR, fill: "none", stroke: "#1f2640", "stroke-width": "6" }),
      prog === null || prog === undefined ? null : s("circle", {
        cx: x, cy: y, r: ringR, fill: "none", stroke: "#67e8f9", "stroke-width": "6", "stroke-linecap": "round",
        "stroke-dasharray": `${(circ * prog) / 100} ${circ}`, transform: `rotate(-90 ${x} ${y})` }),
      s("circle", { class: "planet", cx: x, cy: y, r: pr, fill: color, opacity: "0.9" }),
      s("text", { x, y: y + 5, fill: "#08101c" }, c.code),
      s("text", { class: "pct", x, y: y + pr + 30, fill: color }, c.grade_is_course_grade ? pct(c.grade.current_pct) : "points only"),
      s("title", {}, c.name));
    svg.append(g);
  });
  return svg;
}

function courseTable() {
  return h("div", { class: "table-scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Course", "Current grade", "Band", "Graded so far", "Possible range", "Progress", "Next deadline"].map((t, i) => h("th", { class: i && i < 6 ? "num" : null }, t)))),
    h("tbody", {}, DATA.courses.map((c) => {
      const next = c.deadlines.find((d) => d.days_left !== null && d.days_left >= 0);
      return h("tr", {},
        h("td", {}, h("a", { href: `#/course/${encodeURIComponent(c.code)}/overview` }, c.code), h("div", { class: "muted" }, c.name)),
        h("td", { class: "num" }, c.grade_is_course_grade ? pct(c.grade.current_pct) : "not a course grade"),
        h("td", { class: `num ${bandClass(c.band)}` }, BAND_LABEL[c.band] || "—"),
        h("td", { class: "num" }, pct(c.grade.weight_graded_pct)),
        h("td", { class: "num" }, c.grade_is_course_grade ? `${pct(c.grade.floor_pct)} – ${pct(c.grade.ceiling_pct)}` : "—"),
        h("td", { class: "num" }, c.progress.label || pct(c.progress.course_progress_pct)),
        h("td", {}, next ? `${next.what} · ${fmtDay(next.when)}` : "—"));
    }))));
}

function river(items, withCode) {
  const days = DATA.summary.timeline_days;
  const wrap = h("div", {});
  if (!items.length) return h("p", { class: "muted" }, "Nothing due in this window.");
  const r = h("div", { class: "river", role: "img", "aria-label": `${items.length} deadlines in the next ${days} days` }, h("div", { class: "axis" }));
  [0, 7, 14].filter((d) => d <= days).forEach((d) => r.append(h("div", { class: "tick", style: { left: `${(d / days) * 100}%` } }, d ? `+${d}d` : "today")));
  items.forEach((d, i) => {
    const size = 12 + Math.min(Number(d.pct_of_grade) || 0, 40) * 0.9;
    r.append(h("div", {
      class: `dot${d.status === "exam" ? " exam" : ""}`, tabindex: "0",
      title: `${withCode ? d.code + " · " : ""}${d.what} · ${fmtWhen(d.when)}${d.pct_of_grade !== null ? ` · ${d.pct_of_grade}% of grade` : ""}`,
      style: { left: `${(d.days_left / days) * 100}%`, top: `${i % 2 ? 92 : 48}px`, width: `${size}px`, height: `${size}px` },
    }));
  });
  wrap.append(r, h("ul", { class: "river-list" }, items.map((d) => h("li", {},
    h("span", {}, withCode ? h("strong", {}, `${d.code} `) : null, d.what, d.status === "exam" ? h("span", { class: "chip" }, "exam") : null),
    h("span", { class: "muted" }, `${fmtWhen(d.when)} · ${daysText(d.days_left)}${d.pct_of_grade !== null ? ` · ${d.pct_of_grade}%` : ""}`)))));
  return wrap;
}

// ---------- course page ----------
function renderCourse(c, tab) {
  document.title = `${c.code} · Semester Orbit`;
  app.append(
    h("p", { class: "crumbs" }, h("a", { href: "#/" }, "← All courses")),
    h("header", { class: "top" },
      h("div", {}, h("p", { class: "eyebrow" }, c.progress.label || "Course"), h("h1", {}, h("span", { class: "grad" }, c.code), " ", c.name)),
      h("div", { class: "right" }, h("p", { class: "eyebrow" }, "Current grade"),
        h("div", { class: `big ${bandClass(c.band)}` }, c.grade_is_course_grade ? pct(c.grade.current_pct) : "n/a"),
        h("div", { class: "muted" }, c.grade_is_course_grade ? (BAND_LABEL[c.band] || "") : "Unweighted points total, not the course grade"))),
    h("nav", { class: "tabs", "aria-label": "Course sections" }, TABS.map((t) => h("a", {
      href: `#/course/${encodeURIComponent(c.code)}/${t}`, "aria-current": t === tab ? "page" : null }, t[0].toUpperCase() + t.slice(1)))));
  ({ overview, grades, notes, plan, deadlines, sources })[tab](c);
}

function overview(c) {
  const g = c.grade;
  app.append(h("div", { class: "kpis" },
    kpi("Current grade", c.grade_is_course_grade ? pct(g.current_pct) : "n/a", g.basis),
    kpi("Graded so far", pct(g.weight_graded_pct), "of the final grade"),
    kpi("Possible finish", c.grade_is_course_grade ? `${pct(g.floor_pct)} – ${pct(g.ceiling_pct)}` : "n/a", "0% vs 100% on the rest"),
    kpi("Course progress", pct(c.progress.course_progress_pct), c.progress.label || "")));

  if (c.grade_is_course_grade && g.floor_pct !== null && g.ceiling_pct !== null) {
    const pos = (x) => `${Math.max(0, Math.min(100, x))}%`;
    app.append(h("section", { class: "panel" }, h("h2", {}, "Where you can finish"),
      h("div", { class: "range", role: "img", "aria-label": `Floor ${pct(g.floor_pct)}, current ${pct(g.current_pct)}, ceiling ${pct(g.ceiling_pct)}` },
        h("div", { class: "span", style: { left: pos(g.floor_pct), width: `${g.ceiling_pct - g.floor_pct}%` } }),
        g.current_pct !== null ? h("div", { class: "mark", style: { left: pos(g.current_pct) } }) : null,
        h("div", { class: "lab down", style: { left: pos(g.floor_pct) } }, `floor ${pct(g.floor_pct)}`),
        h("div", { class: "lab down", style: { left: pos(g.ceiling_pct) } }, `ceiling ${pct(g.ceiling_pct)}`),
        g.current_pct !== null ? h("div", { class: "lab up", style: { left: pos(g.current_pct) } }, `now ${pct(g.current_pct)}`) : null)));
  }

  const needed = Object.entries(c.needed || {});
  app.append(h("div", { class: "grid2 row-gap" },
    h("section", { class: "panel" }, h("h2", {}, "What you need on the rest"),
      needed.length && c.grade_is_course_grade ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Target"), h("th", { class: "num" }, "Average needed on everything left"))),
        h("tbody", {}, needed.map(([t, v]) => h("tr", {}, h("td", {}, `${t}%`),
          h("td", { class: "num" }, v === null ? "n/a" : v <= 0 ? "already secured" : v > 100 ? `${pct(v)} (out of reach)` : pct(v))))))
        : h("p", { class: "muted" }, "No targets: needs syllabus weights."),
      c.pass_requirements.length ? [h("h3", {}, "Pass requirements"), ...c.pass_requirements.map((p) => h("div", { class: "card" },
        h("div", { class: "title" }, `${p.components.join(" + ")} ≥ ${p.threshold_pct}%`),
        h("div", { class: "muted" }, `Now ${pct(p.current_pct)} · need ${pct(p.needed_on_remaining_pct)} on what's left · ${p.status.replaceAll("_", " ")}`)))] : null),
    h("section", { class: "panel" }, h("h2", {}, "Coming up"),
      river(c.deadlines.filter((d) => d.days_left !== null && d.days_left >= 0 && d.days_left <= DATA.summary.timeline_days), false))));

  if (c.warnings.length || c.input_requests.length) {
    app.append(h("section", { class: "panel" }, h("h2", {}, "Check these"),
      c.warnings.map((w) => h("div", { class: "card warn" }, w)),
      c.input_requests.map((r) => h("div", { class: "card ask" }, h("div", { class: "title" }, `Enter: ${r.component}`), h("div", { class: "muted" }, r.reason || "")))));
  }
}

function kpi(label, value, sub) {
  return h("div", { class: "kpi" }, h("p", { class: "eyebrow" }, label), h("div", { class: "v" }, value), h("div", { class: "s" }, sub || ""));
}

function grades(c) {
  if (!c.components.length) {
    app.append(h("section", { class: "panel" }, h("p", { class: "muted" }, "No syllabus components yet. Run the syllabus flow for this course.")));
    return;
  }
  app.append(h("section", { class: "panel" }, h("h2", {}, "Components"), h("div", { class: "table-scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Component", "Weight", "Graded", "Drop lowest", "Average", ""].map((t, i) => h("th", { class: i && i < 5 ? "num" : null }, t)))),
    h("tbody", {}, c.components.map((k) => h("tr", {},
      h("td", {}, k.component),
      h("td", { class: "num" }, k.weight_pct === null ? "n/a" : `${k.weight_pct}%`),
      h("td", { class: "num" }, `${k.graded ?? 0}${k.expected !== null && k.expected !== undefined ? ` / ${k.expected}` : ""}`),
      h("td", { class: "num" }, k.drop_lowest || "—"),
      h("td", { class: "num" }, pct(k.average_pct)),
      h("td", {}, k.average_pct === null ? null : h("div", { class: "bar" }, h("span", { style: { width: `${Math.min(100, k.average_pct)}%` } }))))))))));

  c.components.forEach((k) => {
    app.append(h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", {}, k.component), h("span", { class: "muted" }, k.weight_pct === null ? "" : `${k.weight_pct}% of the grade`)),
      k.items.length ? h("table", {}, h("tbody", {}, k.items.map((i) => h("tr", {},
        h("td", {}, i.name),
        h("td", {}, h("span", { class: `chip ${({ dropped: "dropped", counted: "counted", "entered by you": "entered" })[i.status] || ""}` }, i.status)),
        h("td", { class: "num" }, pct(i.pct)),
        h("td", {}, i.pct === null ? null : h("div", { class: "bar" }, h("span", { style: { width: `${Math.min(100, i.pct)}%` } })))))))
        : h("p", { class: "muted" }, "No items in Canvas yet."),
      k.rules.length ? [h("h3", {}, "Syllabus rules (quoted)"), ...k.rules.map((r) => h("div", { class: "card" },
        h("span", { class: "chip" }, r.kind.replaceAll("_", " ")), r.summary, r.quote ? h("blockquote", {}, `“${r.quote}”`) : null))] : null));
  });
}

function notes(c) {
  if (!c.notes.length) { app.append(h("section", { class: "panel" }, h("p", { class: "muted" }, "No lecture material found yet."))); return; }
  app.append(h("section", { class: "panel" }, h("h2", {}, "Notes so far"),
    h("p", { class: "muted" }, "Extracted outlines (titles, learning goals, slide headings), not written summaries."),
    c.notes.map((n) => h("div", { class: "card" },
      h("div", { class: "panel-head" }, h("span", { class: "title" }, n.title), h("span", { class: "chip" }, n.label)),
      n.goals.length ? [h("p", { class: "eyebrow" }, "Learning goals"), h("ul", { class: "tight" }, n.goals.map((x) => h("li", {}, x)))] : null,
      n.points.length ? [h("p", { class: "eyebrow" }, "Covered"), h("ul", { class: "tight" }, n.points.map((x) => h("li", {}, x)))] : null,
      n.scores.length ? h("p", { class: "muted" }, "Your scores: ", n.scores.map((x) => `${x.item} ${pct(x.pct)}`).join(", ")) : null,
      n.source_url && /^https?:\/\//.test(n.source_url) ? h("a", { href: n.source_url, target: "_blank", rel: "noopener noreferrer" }, "Source") : null))));
}

function plan(c) {
  const p = c.plan || {};
  app.append(h("section", { class: "panel" }, h("h2", {}, "Study plan"),
    p.weak_areas && p.weak_areas.length ? h("p", {}, h("span", { class: "muted" }, "Weak areas first: "), p.weak_areas.join(", ")) : null,
    (p.days || []).length ? p.days.map((d) => h("div", { class: "day" },
      h("div", { class: "d" }, fmtDay(`${d.date}T12:00:00`)),
      h("div", {}, d.tasks.map((t) => h("div", {}, h("strong", {}, t.task), h("div", { class: "muted" }, t.why))))))
      : h("p", { class: "muted" }, "Nothing scheduled.")));
}

function deadlines(c) {
  app.append(h("section", { class: "panel" }, h("h2", {}, "Every deadline"), h("div", { class: "table-scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, ["When", "What", "% of grade", "Status", "Sources"].map((t, i) => h("th", { class: i === 2 ? "num" : null }, t)))),
    h("tbody", {}, c.deadlines.map((d) => h("tr", {},
      h("td", {}, fmtWhen(d.when), h("div", { class: "muted" }, daysText(d.days_left))),
      h("td", {}, d.what),
      h("td", { class: "num" }, d.pct_of_grade === null ? "—" : `${d.pct_of_grade}%`),
      h("td", {}, h("span", { class: "chip" }, d.status_label || d.status)),
      h("td", { class: "muted" }, d.sources.join(", ")))))))));
}

function sources(c) {
  app.append(h("section", { class: "panel" }, h("h2", {}, "Where this comes from"),
    c.sources.map((x) => h("div", { class: "card" }, h("div", { class: "title" }, x.name),
      h("div", { class: "muted" }, x.detail, x.last_synced ? ` · synced ${fmtWhen(x.last_synced)}` : ""))),
    h("p", { class: "muted" }, "Canvas is read-only. Marks you enter are labelled \"entered by you\" in every calculation.")));
}

// ---------- ask dialog (agents are paid, so they run from the terminal) ----------
function askDialog() {
  const code = (location.hash.match(/course\/([^/]+)/) || [])[1];
  const q = code ? `What do I need in ${decodeURIComponent(code)} for 80%?` : "What is due this week?";
  const d = h("dialog", { "aria-label": "Ask your course" },
    h("h2", {}, "Ask your course"),
    h("p", { class: "muted" }, "The agents call a paid model, so they run from your terminal (key in .env). Every number in the answer comes from the same Python tools as this page."),
    h("pre", {}, `.venv/bin/python -m scripts.ask --trace "${q}"`),
    h("form", { method: "dialog" }, h("button", {}, "Close")));
  d.addEventListener("close", () => d.remove());
  document.body.append(d);
  d.showModal();
}

// ---------- boot ----------
document.body.append(h("button", { class: "ask-btn", onclick: askDialog }, "Ask your course"));
window.addEventListener("hashchange", route);
fetch("dashboard.json", { cache: "no-store" })
  .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then((d) => { DATA = d; route(); })
  .catch((e) => {
    app.replaceChildren(h("section", { class: "panel" }, h("h2", {}, "No dashboard data"),
      h("p", {}, "Start the page with the local server, which builds the data on each load:"),
      h("pre", {}, ".venv/bin/python -m scripts.serve_web"), h("p", { class: "muted" }, String(e))));
  });
