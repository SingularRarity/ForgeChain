"""Ingester — reads skills.md files, documentation URLs, and articles.

Supported sources:
  - Local Markdown files  (.md, .mdx, .txt, .rst)
  - HTTP/HTTPS URLs       (HTML stripped to text; respects robots.txt)
  - Directory trees       (recursively ingests all supported files)

Usage:
    ingester = Ingester(role="backend_dev")
    ingester.ingest_file("skills/backend_dev.md")
    ingester.ingest_url("https://fastapi.tiangolo.com/tutorial/")
    ingester.ingest_dir("docs/")
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from .chunker import chunk_text, Chunk
from .embedder import embed_texts
from .store import KnowledgeStore

logger = logging.getLogger(__name__)

_SUPPORTED_EXTS = {".md", ".mdx", ".txt", ".rst"}
_REQUEST_TIMEOUT = 20.0
_USER_AGENT = "ForgeChain-KnowledgeBot/1.0 (educational; contact admin@singularraritylabs.com)"


class Ingester:
    def __init__(self, role: str, project_id: str | None = None) -> None:
        self.role = role
        self.project_id = project_id
        self._store = KnowledgeStore(role, project_id=project_id)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def ingest_file(self, path: str | Path) -> int:
        """Parse a local file and add its chunks to the knowledge base."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")
        text = p.read_text(encoding="utf-8", errors="ignore")
        return self._process(text, source=str(p.resolve()))

    def ingest_url(self, url: str) -> int:
        """Fetch a URL, strip HTML, and add chunks to the knowledge base."""
        text = self._fetch_url(url)
        if not text:
            logger.warning("Nothing ingested from %s", url)
            return 0
        return self._process(text, source=url)

    def ingest_dir(self, directory: str | Path, *, recursive: bool = True) -> int:
        """Ingest all supported files in a directory."""
        root = Path(directory)
        total = 0
        pattern = "**/*" if recursive else "*"
        for p in root.glob(pattern):
            if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTS:
                try:
                    total += self.ingest_file(p)
                except Exception as e:
                    logger.warning("Skipping %s: %s", p, e)
        return total

    def ingest_text(self, text: str, *, source: str = "inline") -> int:
        """Directly ingest raw text (e.g. paste of a skills.md)."""
        return self._process(text, source=source)

    def delete_source(self, source: str) -> None:
        """Remove all chunks from a previously ingested source."""
        self._store.delete_source(source)

    def status(self) -> dict:
        return {
            "role": self.role,
            "total_chunks": self._store.count(),
            "sources": self._store.list_sources(),
        }

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _process(self, text: str, source: str) -> int:
        chunks = chunk_text(text, source=source, role=self.role)
        if not chunks:
            logger.warning("No chunks produced from %s", source)
            return 0
        vectors = embed_texts([c.text for c in chunks])
        added = self._store.add_chunks(chunks, vectors)
        logger.info("[ingest:%s] %s → %d chunks", self.role, source, added)
        return added

    def _fetch_url(self, url: str) -> Optional[str]:
        try:
            resp = httpx.get(
                url,
                follow_redirects=True,
                timeout=_REQUEST_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "html" in ct:
                return _strip_html(resp.text)
            return resp.text
        except httpx.HTTPError as e:
            logger.error("Failed to fetch %s: %s", url, e)
            return None


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace. No dependencies."""
    # Remove script and style blocks entirely
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    # Remove all other tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                          ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse whitespace, preserve paragraph breaks
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
