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
    """Retrieve relevant chunks for a (role, query) pair.

    When project_id is provided, results are merged from two sources:
      1. The project-specific KB  → {FORGECHAIN_PROJECTS_PATH}/{project_id}/knowledge_base/
      2. The shared KB            → {FORGECHAIN_PROJECTS_PATH}/shared/knowledge_base/
    This union retrieval gives every project access to both its own knowledge
    and patterns promoted from other projects.

    Without project_id: uses the global KB at FORGECHAIN_KB_PATH (original behaviour).
    """

    def __init__(self, role: str, project_id: str | None = None) -> None:
        self.role = role
        self.project_id = project_id
        self._redis_url = os.getenv("REDIS_URL")

        if project_id:
            self._stores = [
                KnowledgeStore(role, project_id=project_id),   # project-specific
                KnowledgeStore(role, project_id="shared"),     # shared universal
            ]
        else:
            self._stores = [KnowledgeStore(role)]              # original behaviour

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        min_score: float = _DEFAULT_MIN_SCORE,
    ) -> list[dict[str, Any]]:
        """Return ranked chunks most relevant to *query*.

        When multiple stores are configured (project + shared), results from
        both are merged, deduplicated by text content, and re-ranked by score.
        """
        active_stores = [s for s in self._stores if s.count() > 0]
        if not active_stores:
            self._record_coverage_async(query, [])
            return []

        vec = embed_query(query)
        seen_texts: set[str] = set()
        all_hits: list[dict[str, Any]] = []

        for store in active_stores:
            for hit in store.query(vec, top_k=top_k, min_score=min_score):
                if hit["text"] not in seen_texts:
                    seen_texts.add(hit["text"])
                    all_hits.append(hit)

        # Re-rank merged results by score, cap at top_k
        all_hits.sort(key=lambda h: h["score"], reverse=True)
        hits = all_hits[:top_k]

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
        return any(s.count() > 0 for s in self._stores)
