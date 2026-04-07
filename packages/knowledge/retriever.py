"""Retriever — query the knowledge base and format context for DSPy injection."""

from __future__ import annotations

import logging
import os
from typing import Any

from .embedder import embed_query
from .store import KnowledgeStore

logger = logging.getLogger(__name__)

_DEFAULT_TOP_K   = 5
_DEFAULT_MIN_SCORE = 0.30
_MAX_CONTEXT_CHARS = 3000   # cap injected context to stay within Ollama's budget


class Retriever:
    """Retrieve relevant chunks for a (role, query) pair."""

    def __init__(self, role: str) -> None:
        self.role = role
        self._store = KnowledgeStore(role)
        self._redis_url = os.getenv("REDIS_URL")

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        min_score: float = _DEFAULT_MIN_SCORE,
    ) -> list[dict[str, Any]]:
        """Return ranked chunks most relevant to *query*."""
        if self._store.count() == 0:
            self._record_coverage_async(query, [])
            return []
        vec = embed_query(query)
        hits = self._store.query(vec, top_k=top_k, min_score=min_score)
        self._record_coverage_async(query, [h["score"] for h in hits])
        return hits

    def _record_coverage_async(self, query: str, scores: list[float]) -> None:
        """Fire-and-forget coverage recording — never blocks retrieval."""
        if not self._redis_url:
            return
        try:
            import asyncio
            import sys
            for _p in ["/packages", "../../packages"]:
                if _p not in sys.path:
                    sys.path.insert(0, _p)
            from quant.coverage import CoverageTracker
            tracker = CoverageTracker(self._redis_url)
            # If there's a running event loop (async context), schedule as task.
            # Otherwise (sync Celery worker), run a short coroutine.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(tracker.record(self.role, query, scores))
            except RuntimeError:
                asyncio.run(tracker.record(self.role, query, scores))
        except Exception:
            pass  # coverage recording is best-effort

    def retrieve_as_context(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        min_score: float = _DEFAULT_MIN_SCORE,
    ) -> str:
        """Return retrieved chunks formatted as a single context string
        ready for injection into a DSPy prompt field."""
        hits = self.retrieve(query, top_k=top_k, min_score=min_score)
        if not hits:
            return ""

        parts = []
        total = 0
        for i, hit in enumerate(hits, 1):
            heading = f" — {hit['heading']}" if hit.get("heading") else ""
            source  = hit.get("source", "")
            header  = f"[{i}] {source}{heading} (relevance: {hit['score']:.2f})"
            block   = f"{header}\n{hit['text']}"
            if total + len(block) > _MAX_CONTEXT_CHARS:
                break
            parts.append(block)
            total += len(block)

        return "\n\n---\n\n".join(parts)

    def has_knowledge(self) -> bool:
        return self._store.count() > 0
