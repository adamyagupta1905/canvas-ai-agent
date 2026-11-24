// PrairieLearn collector: run inside a signed-in PrairieLearn tab (same origin, GET requests only).
// Returns compact JSON for canvas_agent/prairielearn.py. Reads the student's own pages; never submits anything.
// Privacy: group names, join codes and group members (classmates' emails) are NOT collected.
(async () => {
  const text = (el) => (el ? el.innerText.trim().replace(/\s+/g, " ") : "");
  const get = async (href) => new DOMParser().parseFromString(await (await fetch(href)).text(), "text/html");

  const home = await get("/");
  const instances = [...home.querySelectorAll("a")]
    .map((a) => ({ title: text(a), href: a.getAttribute("href") }))
    .filter((l) => /^\/pl\/course_instance\/\d+$/.test(l.href || ""));

  const out = { collected_at: new Date().toISOString(), origin: location.origin, courses: [] };
  for (const inst of instances) {
    const doc = await get(`${inst.href}/assessments`);
    let set = null;
    const rows = [];
    for (const tr of doc.querySelectorAll("tbody tr")) {
      const cells = [...tr.querySelectorAll("th,td")].map(text);
      if (cells.length === 1) { set = cells[0]; continue; } // set heading row, e.g. "Homeworks"
      const a = tr.querySelector("a");
      rows.push({ set, label: cells[0], title: cells[1], credit_text: cells[2] || "", score_text: cells[3] || "",
                  href: a ? a.getAttribute("href") : null });
    }
    for (const r of rows) {
      if (!r.href) continue;
      const d = await get(r.href);
      const main = d.querySelector("main") || d.body;
      const m = main.innerText.match(/Total points:\s*([\d.]+)\s*\/\s*([\d.]+)/);
      r.points = m ? [parseFloat(m[1]), parseFloat(m[2])] : null;
      r.open = /Assessment is open/.test(main.innerText);
      // question table: keep question names, status and points only (no group/member tables)
      const qtable = [...main.querySelectorAll("table")].find((t) => /Question/.test(text(t.querySelector("tr"))));
      r.questions = qtable
        ? [...qtable.querySelectorAll("tr")].slice(1).map((tr) => [...tr.querySelectorAll("th,td")].map(text))
            .filter((c) => c.length > 1).map((c) => ({ name: c[0], cells: c.slice(1) }))
        : [];
    }
    out.courses.push({ course_instance_id: inst.href.split("/").pop(), title: inst.title, assessments: rows });
  }
  return JSON.stringify(out);
})();
