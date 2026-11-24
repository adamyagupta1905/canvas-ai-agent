"""Read-only Canvas REST client (httpx). The only way the app talks to Canvas.

Auth: a personal access token the student creates in Canvas (Account > Settings > New Access Token),
stored in .env as CANVAS_TOKEN with CANVAS_BASE_URL (e.g. https://canvas.example.edu). Never logged or printed.

Read-only by construction: the class exposes GET helpers only; there is no method that sends
POST/PUT/DELETE, so nothing can submit, post or change anything in the student's account.
"""

from __future__ import annotations

import json
import os
import re

import httpx

_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class CanvasError(RuntimeError):
    pass


class CanvasAPI:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        base_url = (base_url or os.environ.get("CANVAS_BASE_URL", "")).rstrip("/")
        token = token or os.environ.get("CANVAS_TOKEN")
        if not base_url or not token:
            raise CanvasError("Set CANVAS_BASE_URL and CANVAS_TOKEN in .env (see .env.example).")
        self.base_url = base_url
        self._http = httpx.Client(
            base_url=base_url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            transport=transport, timeout=timeout, follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, url: str, params: dict | list | None = None) -> httpx.Response:
        r = self._http.get(url, params=params)
        if r.status_code == 401:
            raise CanvasError("Canvas rejected the token (401). Create a new one and update .env.")
        if r.status_code >= 400:
            raise CanvasError(f"GET {r.request.url.path} -> HTTP {r.status_code}")
        return r

    @staticmethod
    def _json(r: httpx.Response):
        return json.loads(r.text.removeprefix("while(1);"))

    def get(self, path: str, params: dict | list | None = None):
        return self._json(self._get(path, params))

    def get_all(self, path: str, params: dict | list | None = None) -> list:
        """Follow Canvas Link-header pagination until the last page."""
        out: list = []
        r = self._get(path, params)
        while True:
            page = self._json(r)
            if not isinstance(page, list):
                return page
            out.extend(page)
            m = _NEXT.search(r.headers.get("Link", ""))
            if not m:
                return out
            r = self._get(m.group(1))

    # ---- typed helpers -------------------------------------------------------------------------
    def active_courses(self) -> list[dict]:
        return self.get_all("/api/v1/courses", [("enrollment_state", "active"), ("per_page", 100),
                                                ("include[]", "term")])

    def course(self, course_id: int) -> dict:
        return self.get(f"/api/v1/courses/{course_id}", [("include[]", "syllabus_body"), ("include[]", "term")])

    def assignment_groups(self, course_id: int) -> list[dict]:
        return self.get_all(f"/api/v1/courses/{course_id}/assignment_groups",
                            [("include[]", "assignments"), ("include[]", "submission"), ("per_page", 100)])

    def my_enrollment_grades(self, course_id: int) -> dict | None:
        rows = self.get_all(f"/api/v1/courses/{course_id}/enrollments",
                            [("user_id", "self"), ("type[]", "StudentEnrollment")])
        for e in rows:
            if e.get("grades"):
                return e["grades"]
        return None

    def modules(self, course_id: int) -> list[dict]:
        return self.get_all(f"/api/v1/courses/{course_id}/modules", [("include[]", "items"), ("per_page", 100)])

    def tabs(self, course_id: int) -> list[dict]:
        return self.get_all(f"/api/v1/courses/{course_id}/tabs")

    def external_tool(self, course_id: int, tool_id: int) -> dict:
        return self.get(f"/api/v1/courses/{course_id}/external_tools/{tool_id}")

    def sessionless_launch_url(self, course_id: int, tool_id: int) -> str | None:
        r = self.get(f"/api/v1/courses/{course_id}/external_tools/sessionless_launch", [("id", tool_id)])
        return r.get("url") if isinstance(r, dict) else None

    def get_text(self, url: str) -> str:
        return self._get(url).text

    def file_meta(self, file_id: int) -> dict:
        return self.get(f"/api/v1/files/{file_id}")

    def download(self, url: str, max_bytes: int = 50_000_000) -> bytes:
        r = self._get(url)
        if len(r.content) > max_bytes:
            raise CanvasError(f"File larger than {max_bytes} bytes; refusing to download.")
        return r.content
