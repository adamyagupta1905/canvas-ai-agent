"""Serve the Orbit web UI (`web/`) with a freshly exported dashboard JSON. Free, local only.

    .venv/bin/python -m scripts.serve_web            # http://127.0.0.1:8503
    COURSE_DATA_DIR=demo_data .venv/bin/python -m scripts.serve_web

`/dashboard.json` is built on every request by `scripts.export_dashboard.export` from the same data folder the
Streamlit app uses (data/, else the fictional demo). The server binds to 127.0.0.1 only, so real course data never
leaves the machine, and it answers GET/HEAD only.
"""

from __future__ import annotations

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.export_dashboard import data_dir_and_now, export

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
HOST = "127.0.0.1"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, root: Path = ROOT, **kwargs):
        self.data_root = root
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if self.path.split("?")[0] == "/dashboard.json":
            data_dir, now, demo = data_dir_and_now(self.data_root)
            out = export(now, data_dir)
            out["demo"] = demo
            body = json.dumps(out, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._static = True  # page files: always revalidate, so an edited app.js is picked up on reload
        super().do_GET()

    def end_headers(self) -> None:
        if getattr(self, "_static", False):
            self.send_header("Cache-Control", "no-cache")
        # the page loads nothing from other origins; course text is rendered as text, never as HTML
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # keep the terminal quiet
        pass


def make_server(port: int = 8503, root: Path = ROOT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), partial(Handler, root=root))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8503)
    args = ap.parse_args()
    server = make_server(args.port)
    data_dir, _, demo = data_dir_and_now()
    print(f"Orbit UI on http://{HOST}:{args.port}  (data: {data_dir.name}{', fictional demo' if demo else ''}; local only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
