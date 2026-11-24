"""Canvas client + ingest against a fake Canvas server (httpx.MockTransport). No network, no token."""

import json

import httpx
import pytest

from canvas_agent.canvas_api import CanvasAPI, CanvasError
from canvas_agent.grades import grade_summary
from canvas_agent.ingest import course_code, fetch_syllabus, load_pages, snapshot_course, source_from_user_file

BASE = "https://canvas.example.edu"


def _pdf_bytes(text: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, text)
    return bytes(pdf.output())


SYLLABUS_PDF = _pdf_bytes("Assessments Summary Component Weight: Exercises 20%, Midterm exam 30%, Final exam 50%.")


def make_canvas(course_overrides=None, groups=None, modules=None, files=None, grades=None):
    """Returns (transport, seen) where seen records every request method + path."""
    seen = []
    course = {"id": 7, "name": "SYN_V 101 001 2026F Synthetic Course", "course_code": "SYN_V 101 001 2026F",
              "apply_assignment_group_weights": True, "hide_final_grades": False,
              "syllabus_body": '<p>Syllabus: <a href="/courses/7/files/55?wrap=1" '
                               'data-api-endpoint="https://canvas.example.edu/api/v1/courses/7/files/55">syllabus.pdf</a></p>'}
    course.update(course_overrides or {})
    groups = groups if groups is not None else [
        {"name": "Exercises", "group_weight": 20, "rules": {"drop_lowest": 1}, "assignments": [
            {"name": "Ex 1", "due_at": "2026-09-10T06:00:00Z", "points_possible": 10, "omit_from_final_grade": False,
             "submission": {"workflow_state": "graded", "score": 2, "late": False}},
            {"name": "Ex 2", "due_at": "2026-09-17T06:00:00Z", "points_possible": 10, "omit_from_final_grade": False,
             "submission": {"workflow_state": "graded", "score": 9, "late": True}},
            {"name": "Practice", "due_at": None, "points_possible": 10, "omit_from_final_grade": True,
             "submission": {"workflow_state": "graded", "score": 0}},
        ]},
        {"name": "Midterm", "group_weight": 30, "rules": {}, "assignments": [
            {"name": "Midterm", "due_at": None, "points_possible": 50, "submission": {"workflow_state": "graded", "score": 40}}]},
        {"name": "Final", "group_weight": 50, "rules": {}, "assignments": []},
    ]
    files = files if files is not None else {55: {"id": 55, "display_name": "syllabus.pdf", "content-type": "application/pdf",
                                                    "url": f"{BASE}/files/55/download?verifier=x"}}
    grades = grades if grades is not None else {"current_score": 85.0, "final_score": 41.0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("Authorization")))
        path = request.url.path
        if path == "/api/v1/courses":
            page = request.url.params.get("page")
            if page != "2":  # two pages to exercise Link pagination
                return httpx.Response(200, json=[{"id": 1, "name": "Orientation", "course_code": "Orientation", "term": {"name": "Default Term"}}],
                                      headers={"Link": f'<{BASE}/api/v1/courses?page=2>; rel="next"'})
            return httpx.Response(200, json=[dict(course, term={"name": "2026F_V"})])
        if path == "/api/v1/courses/7":
            return httpx.Response(200, text="while(1);" + json.dumps(course))  # Canvas JSON-hijacking prefix
        if path == "/api/v1/courses/7/assignment_groups":
            return httpx.Response(200, json=groups)
        if path == "/api/v1/courses/7/enrollments":
            return httpx.Response(200, json=[{"grades": grades}])
        if path == "/api/v1/courses/7/modules":
            return httpx.Response(200, json=modules or [])
        if path.startswith("/api/v1/files/"):
            fid = int(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=files[fid]) if fid in files else httpx.Response(404)
        if path.startswith("/files/") and path.endswith("/download"):
            return httpx.Response(200, content=SYLLABUS_PDF, headers={"Content-Type": "application/pdf"})
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


def api_for(transport):
    return CanvasAPI(base_url=BASE, token="test-token", transport=transport)


def test_requires_configuration(monkeypatch):
    monkeypatch.delenv("CANVAS_BASE_URL", raising=False)
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    with pytest.raises(CanvasError, match="CANVAS_BASE_URL"):
        CanvasAPI()


def test_pagination_and_read_only(tmp_path):
    transport, seen = make_canvas()
    api = api_for(transport)
    courses = api.active_courses()
    assert [c["id"] for c in courses] == [1, 7]
    snap = snapshot_course(api, 7)
    fetch_syllabus(api, snap, tmp_path)
    assert {m for m, _, _ in seen} == {"GET"}  # never anything but GET
    assert all(auth == "Bearer test-token" for _, _, auth in seen)
    assert not hasattr(api, "post") and not hasattr(api, "put") and not hasattr(api, "delete")


def test_snapshot_feeds_the_grade_engine():
    transport, _ = make_canvas()
    snap = snapshot_course(api_for(transport), 7)
    assert course_code(snap) == "SYN101"
    assert snap["omit_from_final"] == ["Practice"]
    assert snap["groups"][0]["rules"] == {"drop_lowest": 1}
    assert snap["canvas_totals"] == {"current": 85.0, "final": 41.0}
    s = grade_summary("SYN101", snap)
    # Exercises: drop Ex 1 (2/10) -> 9/10 = 90% (w20) -> 18; Midterm 40/50 = 80% (w30) -> 24; (18+24)/50 = 84%
    assert s.current_pct == 84.0
    assert s.canvas_check["match"] is False  # fake Canvas says 85: the mismatch is surfaced, not hidden


def test_hidden_totals_are_not_fetched_as_truth():
    transport, _ = make_canvas(course_overrides={"hide_final_grades": True})
    assert snapshot_course(api_for(transport), 7)["canvas_totals"] is None


def test_syllabus_pdf_linked_on_syllabus_page_is_downloaded(tmp_path):
    transport, _ = make_canvas()
    api = api_for(transport)
    src = fetch_syllabus(api, snapshot_course(api, 7), tmp_path)
    assert src.kind == "pdf" and src.file_id == 55
    assert "Syllabus page" in src.how
    pages = load_pages(src)
    assert "Midterm exam 30%" in pages[0].text


def test_syllabus_found_in_modules_when_not_on_syllabus_page(tmp_path):
    modules = [{"name": "Course Info", "items": [
        {"type": "Page", "title": "Welcome"},
        {"type": "File", "title": "Course Outline 2026F", "content_id": 77}]}]
    files = {77: {"id": 77, "display_name": "outline.pdf", "content-type": "application/pdf", "url": f"{BASE}/files/77/download"}}
    transport, _ = make_canvas(course_overrides={"syllabus_body": "<p>See modules.</p>"}, modules=modules, files=files)
    api = api_for(transport)
    src = fetch_syllabus(api, snapshot_course(api, 7), tmp_path)
    assert src.kind == "pdf" and src.file_id == 77 and "Modules" in src.how


def test_syllabus_typed_into_canvas_is_used_as_text(tmp_path):
    body = "<h2>Grading</h2><table><tr><td>Exercises</td><td>20%</td></tr></table>" + "<p>" + "Course policy text. " * 60 + "</p>"
    transport, _ = make_canvas(course_overrides={"syllabus_body": body}, files={})
    api = api_for(transport)
    src = fetch_syllabus(api, snapshot_course(api, 7), tmp_path)
    assert src.kind == "html"
    assert "Exercises 20%" in load_pages(src)[0].text


def test_nothing_found_asks_for_a_file(tmp_path):
    transport, _ = make_canvas(course_overrides={"syllabus_body": ""}, files={})
    api = api_for(transport)
    src = fetch_syllabus(api, snapshot_course(api, 7), tmp_path)
    assert src.kind == "none" and "ask the user" in src.how


def test_module_titles_with_passwords_are_not_stored():
    modules = [{"name": "Info", "items": [{"type": "SubHeader", "title": "Password for lab: abc"},
                                          {"type": "Page", "title": "Welcome"}]}]
    transport, _ = make_canvas(modules=modules)
    snap = snapshot_course(api_for(transport), 7)
    assert snap["modules"] == [["Info", ["P:Welcome"]]]


def test_bad_token_gives_a_clear_error():
    api = CanvasAPI(base_url=BASE, token="bad", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(CanvasError, match="401"):
        api.active_courses()


def test_user_file_uses_the_same_flow(tmp_path):
    p = tmp_path / "given.pdf"
    p.write_bytes(SYLLABUS_PDF)
    src = source_from_user_file(p)
    assert src.kind == "pdf" and "provided by the user" in src.how
    assert "Final exam 50%" in load_pages(src)[0].text


def test_redirect_tools_resolve_to_course_sites():
    from canvas_agent.ingest import course_site_urls, custom_url_from_launch

    launch_html = '<form><input type="hidden" name="custom_url" value="https://course.example.org/ml201/"></form>'
    assert custom_url_from_launch(launch_html) == "https://course.example.org/ml201/"

    def handler(req):
        p = req.url.path
        if p == "/api/v1/courses/7/tabs":
            return httpx.Response(200, json=[{"type": "external", "label": "Course Website",
                                              "html_url": "/courses/7/external_tools/70"},
                                             {"type": "external", "label": "Quiz tool", "html_url": "/courses/7/external_tools/71"},
                                             {"type": "internal", "label": "Grades", "html_url": "/courses/7/grades"}])
        if p == "/api/v1/courses/7/external_tools/70":
            return httpx.Response(200, json={"url": "https://www.edu-apps.org/redirect"})
        if p == "/api/v1/courses/7/external_tools/71":
            return httpx.Response(200, json={"url": "https://quiz.example.com/lti"})
        if p == "/api/v1/courses/7/external_tools/sessionless_launch":
            return httpx.Response(200, json={"url": f"{BASE}/launch/70"})
        if p == "/launch/70":
            return httpx.Response(200, text=launch_html)
        return httpx.Response(404)

    api = CanvasAPI(base_url=BASE, token="t", transport=httpx.MockTransport(handler))
    sites = course_site_urls(api, 7, front_page_html='<a href="https://piazza.com/x">P</a> <a href="https://site.example.org/c/">Site</a>')
    assert sites == [{"url": "https://site.example.org/c/", "how": "front page link"},
                     {"url": "https://course.example.org/ml201/", "how": "redirect tool 'Course Website'"}]
