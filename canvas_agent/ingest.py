"""Canvas access -> local snapshot + syllabus document, with no manual downloads.

    api = CanvasAPI()                                   # token from .env
    for c in api.active_courses():
        course = snapshot_course(api, c["id"])          # grades, weights, drop rules, omitted items, modules
        doc = fetch_syllabus(api, c["id"], course_json, modules, dest)   # PDF found + downloaded, or HTML text

Syllabus discovery order (first hit wins, every choice recorded in `SyllabusSource.how`):
  1. a PDF linked from the Canvas Syllabus page
  2. a PDF in Modules whose title mentions syllabus/outline
  3. the Syllabus page text itself, when it is long enough to be the syllabus
  4. otherwise: none found, and the caller can ask the user for a file (same flow accepts an uploaded path)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from canvas_agent.canvas_api import CanvasAPI
from canvas_agent.documents import PageText, extract_pages, html_to_pages

_FILE_ID = re.compile(r"/files/(\d+)")
_SYLLABUS_WORD = re.compile(r"syllabus|outline", re.I)
_CODE = re.compile(r"([A-Z]{3,4})(?:_[A-Z])?\s*(\d{3})")


class SyllabusSource(BaseModel):
    kind: Literal["pdf", "html", "none"]
    how: str  # human-readable trace of where it came from
    path: str | None = None
    file_id: int | None = None


def course_code(course: dict) -> str:
    m = _CODE.search(course.get("course_code") or course.get("name") or "")
    return f"{m.group(1)}{m.group(2)}" if m else str(course["id"])


def snapshot_course(api: CanvasAPI, course_id: int) -> dict:
    """Same shape as data/canvas_snapshot.json, so the grade engine and tracker work unchanged."""
    c = api.course(course_id)
    groups_raw = api.assignment_groups(course_id)
    omit: list[str] = []
    groups = []
    for g in groups_raw:
        items = []
        for a in g.get("assignments", []):
            name = (a.get("name") or "").strip()
            if a.get("omit_from_final_grade"):
                omit.append(name)
            sub = a.get("submission") or {}
            if sub.get("excused"):
                omit.append(name)  # excused = does not count, same effect as omitted
            items.append([name, a.get("due_at"), a.get("points_possible"), sub.get("workflow_state", "unsubmitted"),
                          sub.get("score"), bool(sub.get("late"))])
        groups.append({"name": g["name"], "weight": g.get("group_weight") or 0,
                       "rules": g.get("rules") or {}, "items": items})
    totals = None
    if not c.get("hide_final_grades"):
        grades = api.my_enrollment_grades(course_id)
        if grades and grades.get("current_score") is not None:
            totals = {"current": grades.get("current_score"), "final": grades.get("final_score")}
    mods = api.modules(course_id)
    return {
        "canvas_id": course_id, "name": c.get("name"), "course_code": c.get("course_code"),
        "weighted": bool(c.get("apply_assignment_group_weights")),
        "omit_from_final": omit, "canvas_totals": totals, "groups": groups,
        "modules": [[m["name"], [f"{i['type'][0]}:{i.get('title', '').strip()}" for i in m.get("items", [])
                                 if not re.search(r"password", i.get("title", ""), re.I)]] for m in mods],
        "_syllabus_body": c.get("syllabus_body") or "",
        "_module_files": [(i.get("title", ""), i.get("content_id")) for m in mods for i in m.get("items", [])
                          if i.get("type") == "File"],
    }


def _pdf_file_ids_from_html(html: str) -> list[int]:
    ids = []
    for m in re.finditer(r'<a\b[^>]*>', html or "", re.I):
        tag = m.group(0)
        api = re.search(r'data-api-endpoint="([^"]+)"', tag)
        href = re.search(r'href="([^"]+)"', tag)
        fm = _FILE_ID.search((api.group(1) if api else "") or (href.group(1) if href else ""))
        if fm:
            ids.append(int(fm.group(1)))
    return list(dict.fromkeys(ids))


def fetch_syllabus(api: CanvasAPI, snapshot: dict, dest_dir: Path, min_html_chars: int = 800) -> SyllabusSource:
    dest_dir.mkdir(parents=True, exist_ok=True)
    code = course_code({"id": snapshot["canvas_id"], "course_code": snapshot.get("course_code"), "name": snapshot.get("name")})

    def _download(file_id: int, how: str) -> SyllabusSource | None:
        meta = api.file_meta(file_id)
        if "pdf" not in (meta.get("content-type") or "") and not (meta.get("display_name") or "").lower().endswith(".pdf"):
            return None
        path = dest_dir / f"{code}.pdf"
        path.write_bytes(api.download(meta["url"]))
        return SyllabusSource(kind="pdf", how=f"{how}: {meta.get('display_name')}", path=str(path), file_id=file_id)

    body = snapshot.get("_syllabus_body", "")
    for fid in _pdf_file_ids_from_html(body):
        src = _download(fid, "PDF linked on the Canvas Syllabus page")
        if src:
            return src
    for title, fid in snapshot.get("_module_files", []):
        if fid and _SYLLABUS_WORD.search(title or ""):
            src = _download(int(fid), "Syllabus PDF found in Modules")
            if src:
                return src
    pages = html_to_pages(body)
    if pages and len(pages[0].text) >= min_html_chars:
        path = dest_dir / f"{code}.syllabus.txt"
        path.write_text(pages[0].text)
        return SyllabusSource(kind="html", how="Syllabus page text in Canvas (no PDF)", path=str(path))
    return SyllabusSource(kind="none", how="No syllabus PDF or substantial Syllabus page found; ask the user for a file")


def load_pages(src: SyllabusSource) -> list[PageText]:
    """Turn any syllabus source (downloaded PDF, Canvas HTML text, or a user-supplied file) into pages."""
    if src.kind == "pdf" and src.path:
        return extract_pages(src.path)
    if src.kind == "html" and src.path:
        return [PageText(page=1, text=Path(src.path).read_text(), method="html")]
    return []


def source_from_user_file(path: str | Path) -> SyllabusSource:
    """User-provided input goes through exactly the same flow as a Canvas download."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        return SyllabusSource(kind="pdf", how=f"File provided by the user: {p.name}", path=str(p))
    return SyllabusSource(kind="html", how=f"Text file provided by the user: {p.name}", path=str(p))


def custom_url_from_launch(html: str) -> str | None:
    """A Canvas 'redirect' external tool (e.g. 'Course Website') carries its real target in the launch form."""
    m = re.search(r'name="custom_url"\s+value="([^"]+)"', html) or re.search(r'value="([^"]+)"\s+name="custom_url"', html)
    return m.group(1).replace("&amp;", "&") if m else None


def course_site_urls(api: CanvasAPI, course_id: int, front_page_html: str = "") -> list[dict]:
    """Course-site candidates for a course: external links on the front page, plus the real targets of redirect
    tools in the course navigation (resolved via a sessionless launch; nothing is submitted)."""
    from canvas_agent.website import discover_site_urls

    out = [{"url": u, "how": "front page link"} for u in discover_site_urls(_hrefs(front_page_html))]
    for tab in api.tabs(course_id):
        if tab.get("type") != "external":
            continue
        m = re.search(r"/external_tools/(\d+)", tab.get("html_url", ""))
        if not m:
            continue
        tool_id = int(m.group(1))
        try:
            tool = api.external_tool(course_id, tool_id)
            if "redirect" not in (tool.get("url") or ""):
                continue
            launch = api.sessionless_launch_url(course_id, tool_id)
            target = custom_url_from_launch(api.get_text(launch)) if launch else None
        except Exception:  # a tool we cannot inspect is skipped, never guessed
            continue
        if target and discover_site_urls([target]):
            out.append({"url": target, "how": f"redirect tool '{tab.get('label')}'"})
    seen, uniq = set(), []
    for o in out:
        if o["url"] not in seen:
            seen.add(o["url"])
            uniq.append(o)
    return uniq


def _hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"]+)"', html or "")
