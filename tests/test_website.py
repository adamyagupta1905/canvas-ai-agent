"""Course-website pipeline against a fake site (httpx.MockTransport). No network."""

import httpx

from canvas_agent.component_check import ComponentCheck, past_exams_without_score
from canvas_agent.prairielearn import PLAssessment, pl_course
from canvas_agent.syllabus_flow import _number_in
from canvas_agent.website import (SiteConfig, build_index, classify, crawl, discover_site_urls, find_doc,
                                  html_to_markdown, schedule_rows, search)

BASE = "https://course.example.edu"


def _pdf(text: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, text)
    return bytes(pdf.output())


PAGE = """<html><head><title>{title} | SYN 310</title></head><body><nav><a href="/310/w1/">Home</a></nav>
<div class="vp-doc">{body}</div><a href="/assets/app.js">js</a></body></html>"""
SITE = {
    "/robots.txt": "User-agent: *\nDisallow: /310/w1/private/\n",
    "/310/w1/": PAGE.format(title="Home", body='<h1>Welcome</h1><a href="syllabus">Syllabus</a> <a href="schedule">Schedule</a> '
                                               '<a href="private/notes">Private</a> <a href="https://piazza.com/x">Piazza</a> '
                                               '<a href="/other-course/">Other course</a>'),
    "/310/w1/syllabus": PAGE.format(title="Syllabus", body="<h2>Grading</h2><table><tr><th>Component</th><th>Weight</th></tr>"
                                                           "<tr><td>Labs</td><td>30%</td></tr><tr><td>Final exam</td><td>70%</td></tr></table>"
                                                           "<p>The lowest lab mark is dropped.</p>"),
    "/310/w1/schedule": PAGE.format(title="Schedule", body='<h2>Week by week</h2><table><tr><th>Wk</th><th>Dates</th><th>Lectures</th></tr>'
                                                           '<tr><td>1</td><td>Sep 8</td><td><a href="lectures/01-coupling.pdf">Coupling</a></td></tr></table>'),
    "/310/w1/lectures/01-coupling.pdf": _pdf("Coupling: the degree of interdependence between modules. Low coupling is good."),
    "/310/w1/private/notes": PAGE.format(title="Private", body="<p>should not be fetched</p>"),
}


def make_transport(seen):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, req.headers.get("if-none-match")))
        body = SITE.get(req.url.path)
        if body is None:
            return httpx.Response(404, text="nope")
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        if isinstance(body, bytes):
            return httpx.Response(200, content=body, headers={"content-type": "application/pdf", "etag": '"v1"'})
        ctype = "text/plain" if req.url.path.endswith(".txt") else "text/html; charset=utf-8"
        return httpx.Response(200, text=body, headers={"content-type": ctype, "etag": '"v1"'})
    return httpx.MockTransport(handler)


def run_crawl(tmp_path, seen):
    client = httpx.Client(transport=make_transport(seen), follow_redirects=True)
    cfg = SiteConfig(code="SYN310", start_urls=[f"{BASE}/310/w1/"], scopes=["/310/w1/"])
    return crawl(cfg, tmp_path / "site", client=client, sleep=lambda s: None)


def test_crawl_stays_in_scope_respects_robots_and_saves_pdfs(tmp_path):
    seen = []
    m = run_crawl(tmp_path, seen)
    urls = {d.url: d for d in m.docs}
    assert set(urls) == {f"{BASE}/310/w1/", f"{BASE}/310/w1/syllabus", f"{BASE}/310/w1/schedule",
                         f"{BASE}/310/w1/lectures/01-coupling.pdf"}
    assert {"url": f"{BASE}/310/w1/private/notes", "reason": "robots.txt"} in m.skipped
    assert not any(p.endswith(".js") or "other-course" in p for _, p, _ in seen)  # assets and out-of-scope never fetched
    assert "https://piazza.com/x" in m.external_links
    assert {m_ for m_, _, _ in seen} == {"GET"}
    assert urls[f"{BASE}/310/w1/lectures/01-coupling.pdf"].kind == "lecture"
    assert (tmp_path / "site" / urls[f"{BASE}/310/w1/lectures/01-coupling.pdf"].path).read_bytes()[:4] == b"%PDF"


def test_recrawl_uses_etags_and_keeps_unchanged_docs(tmp_path):
    run_crawl(tmp_path, [])
    seen = []
    m = run_crawl(tmp_path, seen)
    assert len(m.docs) == 4
    assert all(tag == '"v1"' for _, p, tag in seen if p != "/robots.txt")  # conditional requests -> 304s


def test_html_tables_and_headings_survive_as_text():
    title, text, headings, tables, _ = html_to_markdown(SITE["/310/w1/syllabus"])
    assert title == "Syllabus" and headings == ["Grading"]
    assert "## Grading" in text and "Labs | 30%" in text and "Final exam | 70%" in text
    assert tables[0][0] == ["Component", "Weight"]


def test_schedule_rows_index_and_search(tmp_path):
    run_crawl(tmp_path, [])
    site = tmp_path / "site"
    rows = schedule_rows(find_doc(site, "schedule"))
    assert rows == [{"Wk": "1", "Dates": "Sep 8", "Lectures": "Coupling"}]
    assert build_index(site, "SYN310")
    hits = search(site, "interdependence between modules", k=1)
    assert hits[0]["kind"] == "lecture" and hits[0]["url"].endswith("01-coupling.pdf") and hits[0]["page"] == 1


def test_classify_and_discover():
    assert classify("https://x/310/syllabus", "") == "syllabus"
    assert classify("https://x/310/project/d3-design", "") == "project"
    assert classify("https://x/310/lectures/02a.pdf", "") == "lecture"
    links = ["https://course.example.org/cs101/", "https://canvas.example.edu/courses/1", "https://piazza.com/c",
             "https://github.com/x", "/relative"]
    assert discover_site_urls(links) == ["https://course.example.org/cs101/"]


def test_singular_lowest_counts_as_one():
    assert _number_in("The lowest lab mark is dropped.", 1)
    assert _number_in("Your two lowest scores will be dropped.", 2)
    assert not _number_in("The lowest lab mark is dropped.", 2)


def _pl(label, set_, status, earned, possible, deadline=None):
    return PLAssessment(course="SYN310", set=set_, label=label, title=label, kind="graded", status=status, earned=earned,
                        possible=possible, pct=None, pending_questions=0, pending_points=0, credit_pct=None,
                        deadline=deadline, available_from=None, open=status == "open_not_done")


def test_pl_course_uses_closed_zeros_and_canvas_released_scores():
    pl = [_pl("LAB01", "Lab Assignments", "graded", 6, 6), _pl("LAB06", "Lab Assignments", "open_not_done", 0, 15),
          _pl("DELIV1", "Project Deliverables", "closed_no_credit", 0, 8)]
    canvas = {"groups": [{"items": [["LAB06", None, 100, "graded", 0, False]]}]}
    c = pl_course(pl, "SYN310", canvas=canvas)
    items = {it[0]: it for g in c["groups"] for it in g["items"]}
    assert items["Lab Assignments: LAB06 LAB06"][3:5] == ["graded", 0.0]  # from Canvas
    assert items["Project Deliverables: DELIV1 DELIV1"][3:5] == ["graded", 0.0]  # window closed: real zero


def test_past_exam_without_score_is_flagged():
    chk = ComponentCheck(component="Midterm", syllabus_weight=25, canvas_groups=[], canvas_weight=None, items_in_canvas=0,
                         scored=0, expected_items=1, remaining_by_syllabus=1, numbering_gaps=[], drop_syllabus=None,
                         drop_canvas=None, aggregation_syllabus=None, equal_points_in_canvas=True, completion_based=False,
                         avg_canvas_rules=None, avg_syllabus_rules=None, items=[], flags=[], rules=[])
    out = past_exams_without_score([{"kind": "midterm", "exam_date": None}], [{"kind": "midterm", "found_date": "2026-10-29"}],
                                   [chk], "2026-11-23", ["Gradescope"])
    assert out == ["Midterm (25%) was scheduled on or around 2026-10-29 but has no score in Canvas or PrairieLearn yet. Check Gradescope."]


def _doc(url, kind, tables):
    from canvas_agent.website import SiteDoc

    return SiteDoc(url=url, kind=kind, title="t", path="pages/x.md", sha256="x", bytes=1, fetched_at="now", tables=tables)


def test_deadline_rows_handle_different_header_styles():
    from canvas_agent.website import deadline_rows

    a = _doc("u", "page", [[["Due", "Date", "What"], ["D4", "Fri 27 Nov, 18:00", "Build it"]],
                          [["Wk", "Dates", "Due"], ["3", "Sep 22", "D1 Fri Sep 25"]]])  # weekly table is not a deadline table
    b = _doc("u", "page", [[["Assessment", "Due date"], ["hw1", "Sept 14, 11:59 pm"], ["Final exam", ""]]])
    assert deadline_rows(a) == [{"what": "D4 Build it", "when": "Fri 27 Nov, 18:00"}]
    assert deadline_rows(b) == [{"what": "hw1", "when": "Sept 14, 11:59 pm"}]  # no date given: left out, not guessed


def test_schedule_fallback_ignores_textbook_data_tables(tmp_path):
    import json

    from canvas_agent.website import find_doc

    site = tmp_path / "s"
    site.mkdir()
    home = _doc("https://x/c/", "page", [[["Assessment", "Due date"], ["hw1", "Sep 14"]], [["Week", "Dates"], ["1", "Sep 9"]]])
    book = _doc("https://x/book/ch6", "textbook", [[["Date", "Sales", "Due"], ["2020-01-01", "5", "x"]] * 50])
    (site / "manifest.json").write_text(json.dumps({"code": "C", "start_urls": ["https://x/c/"], "scopes": [], "crawled_at": "n",
                                                    "docs": [book.model_dump(), home.model_dump()], "skipped": [], "external_links": []}))
    assert find_doc(site, "schedule").url == "https://x/c/"


def test_lecture_pdfs_with_hashed_names():
    from canvas_agent.course_view import _deck_title, _is_title

    assert classify("https://x/c/_public/Lec07-409e7435ce653ddeb312d398d2186684.pdf", "") == "lecture"
    assert _deck_title("https://x/c/_public/Lec07-409e7435ce653ddeb312d398d2186684.pdf") == "Lecture 7"
    assert _is_title("Overfitting and underfitting") and _is_title("Error and accuracy")
    assert not _is_title("ABojOuIudL") and not _is_title("S PIO v PIO mee C PIO T PIO") and not _is_title("UONepljeA-SSOJD")
