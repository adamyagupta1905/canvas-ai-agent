"""Course-website pipeline: crawl a public course site, keep its text, tables and linked PDFs, and expose it to the
syllabus flow, retrieval and agents. Python only; no LLM here.

    crawl(SiteConfig(code="CS101", start_urls=[".../cs101/"], scopes=["/cs101/", "/textbook/"]), out_dir)

Crawling rules (polite and bounded):
- same host only, and only paths under the configured scopes; everything else is recorded as an outbound link, not fetched
- robots.txt respected (a missing robots.txt means allowed); fixed delay between requests; page and file caps
- HTML: main content only (VitePress/Docusaurus/MkDocs/Jekyll containers, else <main>/<article>/<body>); headings kept
  as markdown, tables kept as "a | b | c" rows so weights and dates stay on one line
- PDFs (lecture slides, handouts) downloaded once; ETag / Last-Modified used so a re-crawl only fetches what changed
- every saved document records its URL, so every later fact can cite where it came from
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel

from canvas_agent.documents import PageText, clean_text, extract_pages

USER_AGENT = "course-study-agent/0.1 (personal study tool; read-only)"
_SKIP_EXT = re.compile(r"\.(js|css|woff2?|ttf|ico|png|jpe?g|gif|svg|webp|mp4|zip|json|xml|map)$", re.I)
_FILE_EXT = re.compile(r"\.(pdf)$", re.I)
_MAIN_SELECTORS = [".vp-doc", ".theme-doc-markdown", ".md-content", "article", "main", ".post-content", "#content"]


@dataclass
class SiteConfig:
    code: str
    start_urls: list[str]
    scopes: list[str] = field(default_factory=list)  # path prefixes that may be fetched
    max_pages: int = 200
    max_files: int = 80
    delay_s: float = 0.3


class SiteDoc(BaseModel):
    url: str
    kind: str  # syllabus | schedule | project | lecture | textbook | page | file
    title: str
    path: str  # saved file, relative to the site folder
    sha256: str
    bytes: int
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: str
    headings: list[str] = []
    tables: list[list[list[str]]] = []
    links_out: list[str] = []


class Manifest(BaseModel):
    code: str
    start_urls: list[str]
    scopes: list[str]
    crawled_at: str
    docs: list[SiteDoc]
    skipped: list[dict]  # url + reason (out of scope, robots, cap, error)
    external_links: list[str]


def classify(url: str, title: str) -> str:
    u, t = url.lower(), title.lower()
    if u.endswith(".pdf"):
        return "lecture" if "/lecture" in u or "slides" in u else "file"
    for kind, pat in [("syllabus", r"syllabus|course outline"), ("schedule", r"schedule|calendar"),
                      ("project", r"/project|deliverable|/d\d|spec"), ("textbook", r"textbook|/book/|/notes/")]:
        if re.search(pat, u) or re.search(pat, t):
            return kind
    return "page"


def html_to_markdown(html: str) -> tuple[str, str, list[str], list[list[list[str]]], list[str]]:
    """-> (title, text, headings, tables, hrefs). Main content only; tables flattened row by row."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "").split("|")[0].strip()
    main = next((soup.select_one(s) for s in _MAIN_SELECTORS if soup.select_one(s)), soup.body or soup)
    hrefs = [a.get("href") for a in soup.find_all("a") if a.get("href")]  # whole page: nav links matter for crawling
    for bad in main.select("script, style, nav, .header-anchor, button"):
        bad.decompose()
    tables = []
    for t in main.find_all("table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in t.find_all("tr")]
        tables.append([r for r in rows if any(r)])
        t.replace_with(soup.new_string("\n\n" + "\n".join(" | ".join(r) for r in rows if any(r)) + "\n\n"))
    headings = []
    for h in main.find_all(re.compile(r"^h[1-4]$")):
        text = h.get_text(" ", strip=True).replace("​", "").strip()
        headings.append(text)
        h.replace_with(soup.new_string(f"\n\n{'#' * int(h.name[1])} {text}\n\n"))
    for li in main.find_all("li"):
        li.insert_before(soup.new_string("\n- "))
    for blk in main.find_all(["p", "div", "br", "pre", "blockquote"]):
        blk.insert_after(soup.new_string("\n"))
    text = clean_text(main.get_text("", strip=False).replace("​", ""))
    return title or (headings[0] if headings else ""), text, headings, tables, hrefs


def _slug(url: str) -> str:
    p = urlparse(url).path.strip("/") or "index"
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", p)[:150]


def _in_scope(url: str, host: str, scopes: list[str]) -> bool:
    u = urlparse(url)
    return u.scheme in ("http", "https") and u.netloc == host and (not scopes or any(u.path.startswith(s) for s in scopes))


def crawl(cfg: SiteConfig, out_dir: Path, client: httpx.Client | None = None, sleep=time.sleep) -> Manifest:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pages").mkdir(exist_ok=True)
    (out_dir / "files").mkdir(exist_ok=True)
    old: dict[str, SiteDoc] = {}
    if (out_dir / "manifest.json").exists():
        old = {d.url: d for d in Manifest.model_validate_json((out_dir / "manifest.json").read_text()).docs}

    own_client = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30)
    host = urlparse(cfg.start_urls[0]).netloc
    robots = RobotFileParser()
    try:
        r = client.get(f"{urlparse(cfg.start_urls[0]).scheme}://{host}/robots.txt")
        robots.parse(r.text.splitlines() if r.status_code == 200 else [])
    except httpx.HTTPError:
        robots.parse([])

    queue = [urldefrag(u)[0] for u in cfg.start_urls]
    seen: set[str] = set()
    docs: list[SiteDoc] = []
    skipped: list[dict] = []
    external: set[str] = set()
    pages = files = 0
    try:
        while queue:
            url = queue.pop(0)
            norm = url.rstrip("/") if not url.endswith(".pdf") else url
            if norm in seen:
                continue
            seen.add(norm)
            if not robots.can_fetch(USER_AGENT, url):
                skipped.append({"url": url, "reason": "robots.txt"})
                continue
            is_file = bool(_FILE_EXT.search(urlparse(url).path))
            if (is_file and files >= cfg.max_files) or (not is_file and pages >= cfg.max_pages):
                skipped.append({"url": url, "reason": "cap reached"})
                continue
            headers = {}
            prev = old.get(url)
            if prev and prev.etag:
                headers["If-None-Match"] = prev.etag
            if prev and prev.last_modified:
                headers["If-Modified-Since"] = prev.last_modified
            try:
                resp = client.get(url, headers=headers)
            except httpx.HTTPError as e:
                skipped.append({"url": url, "reason": f"error: {type(e).__name__}"})
                continue
            sleep(cfg.delay_s)
            if resp.status_code == 304 and prev:  # unchanged since last crawl
                docs.append(prev)
                files += is_file
                pages += not is_file
                for h in prev.links_out:
                    if h not in seen:
                        queue.append(h)
                continue
            if resp.status_code != 200:
                skipped.append({"url": url, "reason": f"HTTP {resp.status_code}"})
                continue
            ctype = resp.headers.get("content-type", "")
            now = datetime.now(timezone.utc).isoformat()
            common = dict(url=url, sha256=hashlib.sha256(resp.content).hexdigest(), bytes=len(resp.content),
                          etag=resp.headers.get("etag"), last_modified=resp.headers.get("last-modified"), fetched_at=now)
            if is_file or "pdf" in ctype:
                rel = f"files/{_slug(url)}"
                (out_dir / rel).write_bytes(resp.content)
                docs.append(SiteDoc(kind=classify(url, ""), title=Path(urlparse(url).path).name, path=rel, **common))
                files += 1
                continue
            if "html" not in ctype:
                skipped.append({"url": url, "reason": f"content-type {ctype}"})
                continue
            title, text, headings, tables, hrefs = html_to_markdown(resp.text)
            links = []
            for h in hrefs:
                absu = urldefrag(urljoin(url, h))[0]
                if _SKIP_EXT.search(urlparse(absu).path):
                    continue
                if _in_scope(absu, host, cfg.scopes):
                    links.append(absu)
                    if absu.rstrip("/") not in seen:
                        queue.append(absu)
                elif absu.startswith("http"):
                    external.add(absu)
            rel = f"pages/{_slug(url)}.md"
            (out_dir / rel).write_text(f"<!-- source: {url} -->\n# {title}\n\n{text}\n")
            docs.append(SiteDoc(kind=classify(url, title), title=title, path=rel, headings=headings, tables=tables,
                                links_out=sorted(set(links)), **common))
            pages += 1
    finally:
        if own_client:
            client.close()
    m = Manifest(code=cfg.code, start_urls=cfg.start_urls, scopes=cfg.scopes,
                 crawled_at=datetime.now(timezone.utc).isoformat(), docs=docs, skipped=skipped,
                 external_links=sorted(external))
    (out_dir / "manifest.json").write_text(m.model_dump_json(indent=1))
    return m


# ---- reading the saved site --------------------------------------------------------------------------------
def load_manifest(site_dir: Path) -> Manifest | None:
    p = site_dir / "manifest.json"
    return Manifest.model_validate_json(p.read_text()) if p.exists() else None


def doc_pages(site_dir: Path, doc: SiteDoc) -> list[PageText]:
    """Pages for any saved document: HTML pages become one 'html' page; PDFs go through text-layer/OCR."""
    p = site_dir / doc.path
    if doc.path.startswith("files/"):
        return extract_pages(p)
    text = p.read_text()
    text = re.sub(r"^<!-- source: .*? -->\n", "", text)
    return [PageText(page=1, text=text, method="html")]


def find_doc(site_dir: Path, kind: str) -> SiteDoc | None:
    m = load_manifest(site_dir)
    if not m:
        return None
    cands = [d for d in m.docs if d.kind == kind]
    return min(cands, key=lambda d: len(d.url)) if cands else None  # shortest URL = the main page of that kind


def schedule_rows(doc: SiteDoc) -> list[dict]:
    """Schedule tables as dicts keyed by their header row, e.g. {'Due': 'D4', 'Date': 'Fri 27 Nov, 18:00', ...}."""
    out = []
    for t in doc.tables:
        if len(t) < 2:
            continue
        head = t[0]
        if not any(re.search(r"date|due|wk|week|when", h, re.I) for h in head):
            continue
        for r in t[1:]:
            out.append({head[i] if i < len(head) else f"col{i}": v for i, v in enumerate(r)})
    return out


def discover_site_urls(links: list[str], ignore_hosts: tuple[str, ...] = ("canvas.", "prairielearn.", "piazza.",
                                                                         "iclicker.", "zoom.", "github.com", "gradescope")) -> list[str]:
    """Course-site candidates from a Canvas front page: external http(s) links not on known platform hosts."""
    out = []
    for u in links:
        if not u or not u.startswith("http"):
            continue
        host = urlparse(u).netloc.lower()
        if any(h in host for h in ignore_hosts):
            continue
        out.append(u)
    return list(dict.fromkeys(out))


def build_index(site_dir: Path, code: str) -> list[dict]:
    """Extract text once (OCR included) and save page-cited chunks to index.json, so search is fast at question time."""
    from canvas_agent.retrieval import chunk_pages

    m = load_manifest(site_dir)
    if not m:
        return []
    chunks = []
    for d in m.docs:
        doc_id = f"{code}-{_slug(d.url)[-60:]}"
        for ch in chunk_pages(doc_id, doc_pages(site_dir, d)):
            chunks.append({"id": ch.id, "url": d.url, "kind": d.kind, "title": d.title, "page": ch.page, "text": ch.text})
    (site_dir / "index.json").write_text(json.dumps(chunks))
    return chunks


def search(site_dir: Path, query: str, k: int = 5, kinds: list[str] | None = None) -> list[dict]:
    """BM25 over the saved index; returns page-cited snippets."""
    from canvas_agent.retrieval import BM25Index, Chunk

    p = site_dir / "index.json"
    if not p.exists():
        return []
    rows = [r for r in json.loads(p.read_text()) if not kinds or r["kind"] in kinds]
    by_id = {r["id"]: r for r in rows}
    hits = BM25Index([Chunk(id=r["id"], page=r["page"], text=r["text"]) for r in rows]).search(query, k=k)
    return [{"title": by_id[h.chunk.id]["title"], "kind": by_id[h.chunk.id]["kind"], "url": by_id[h.chunk.id]["url"],
             "page": h.chunk.page, "snippet": h.chunk.text[:500], "score": round(h.score, 2)} for h in hits]
