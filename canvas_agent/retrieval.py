"""Chunking + retrieval (the "R" in RAG). Stage 3-4 of the standard document flow.

Chunks keep their page number and a stable id (e.g. LAW201-p3-c1) so every extracted fact can
point back to the exact text it came from. Retrieval is BM25 (lexical, offline, free, deterministic),
behind a small Protocol so an embedding retriever can replace it later without touching callers.
"""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel

from canvas_agent.documents import PageText

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?%?")


class Chunk(BaseModel):
    id: str
    page: int
    text: str


class Hit(BaseModel):
    chunk: Chunk
    score: float


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def chunk_pages(doc_id: str, pages: list[PageText], max_chars: int = 1200, overlap: int = 200) -> list[Chunk]:
    """Split each page on blank lines, pack paragraphs up to max_chars, carry `overlap` chars forward.
    Chunks never cross a page boundary, so page citations stay exact."""
    chunks: list[Chunk] = []
    for p in pages:
        if not p.text:
            continue
        paras = [x.strip() for x in re.split(r"\n\s*\n", p.text) if x.strip()]
        buf, n = "", 0
        for para in paras:
            while len(para) > max_chars:  # very long paragraph: hard split
                head, para = para[:max_chars], para[max_chars - overlap:]
                if buf:
                    n += 1
                    chunks.append(Chunk(id=f"{doc_id}-p{p.page}-c{n}", page=p.page, text=buf))
                    buf = ""
                n += 1
                chunks.append(Chunk(id=f"{doc_id}-p{p.page}-c{n}", page=p.page, text=head))
            if buf and len(buf) + len(para) + 2 > max_chars:
                n += 1
                chunks.append(Chunk(id=f"{doc_id}-p{p.page}-c{n}", page=p.page, text=buf))
                buf = buf[-overlap:] + "\n\n" + para if overlap else para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            n += 1
            chunks.append(Chunk(id=f"{doc_id}-p{p.page}-c{n}", page=p.page, text=buf))
    return chunks


class Retriever(Protocol):
    def search(self, query: str, k: int = 4) -> list[Hit]: ...


class BM25Index:
    def __init__(self, chunks: list[Chunk]):
        from rank_bm25 import BM25Okapi

        self.chunks = chunks
        self._tokens = [set(tokenize(c.text)) for c in chunks]
        self._bm25 = BM25Okapi([tokenize(c.text) or ["_"] for c in chunks]) if chunks else None

    def search(self, query: str, k: int = 4) -> list[Hit]:
        if not self._bm25:
            return []
        q = tokenize(query)
        scores = self._bm25.get_scores(q)
        qs = set(q)
        # keep chunks that share query words; in tiny corpora BM25's idf can be 0 for a word in half the documents,
        # so overlap (then score) decides, not "score > 0"
        cands = [i for i in range(len(self.chunks)) if self._tokens[i] & qs]
        ranked = sorted(cands, key=lambda i: (scores[i], len(self._tokens[i] & qs)), reverse=True)[:k]
        return [Hit(chunk=self.chunks[i], score=float(scores[i])) for i in ranked]


_PCT = re.compile(r"\d+(?:\.\d+)?\s*%")


def percent_dense(chunks: list[Chunk], min_count: int = 3) -> list[Chunk]:
    """Chunks with several percentages: what grade-weight tables look like, whatever their wording."""
    return [c for c in chunks if len(_PCT.findall(c.text)) >= min_count]


def with_neighbors(chunks: list[Chunk], selected: list[Chunk], after: int = 1) -> list[Chunk]:
    """Add the next `after` chunk(s) in reading order, so a table cut by a chunk boundary stays whole."""
    pos = {c.id: i for i, c in enumerate(chunks)}
    keep = {c.id for c in selected}
    for c in selected:
        i = pos.get(c.id)
        if i is not None:
            keep.update(chunks[j].id for j in range(i + 1, min(i + 1 + after, len(chunks))))
    return [c for c in chunks if c.id in keep]


def multi_query(retriever: Retriever, queries: list[str], k_per_query: int = 3, max_chunks: int = 8) -> list[Chunk]:
    """Union of top hits across several phrasings, best-scoring first, de-duplicated."""
    best: dict[str, Hit] = {}
    for q in queries:
        for h in retriever.search(q, k=k_per_query):
            if h.chunk.id not in best or h.score > best[h.chunk.id].score:
                best[h.chunk.id] = h
    ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)[:max_chunks]
    return sorted((h.chunk for h in ranked), key=lambda c: (c.page, c.id))
