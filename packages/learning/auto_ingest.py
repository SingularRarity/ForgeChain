"""AutoIngestor — chunk approved patches back into the role's ChromaDB KB.

Every approved PR becomes new knowledge. The patch + its task description
are formatted as a document, chunked, embedded, and upserted into the
role-specific ChromaDB collection. Workers retrieve this on the next
similar task — closing the self-improvement loop.

Source key format:  ``patch:{task_id}``
This allows targeted deletion if a patch is later found to be wrong.
"""

from __future__ import annotations

import asyncio
import logging

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from knowledge.chunker import chunk_text, Chunk
from knowledge.embedder import embed_texts
from knowledge.store import KnowledgeStore
from quant.entropy import filter_by_entropy

import os as _os
_REDIS_URL = _os.getenv("REDIS_URL")

logger = logging.getLogger(__name__)

_PATCH_TEMPLATE = """\
## Approved Patch — {role}

**Task:** {description}

**Patch:**
```diff
{patch}
```
"""


class AutoIngestor:
    """Ingest an approved patch into the role's ChromaDB knowledge base."""

    def ingest_sync(
        self,
        task_id: str,
        role: str,
        description: str,
        patch: str,
    ) -> int:
        """Synchronous variant — callable from Celery workers."""
        return asyncio.run(self.ingest(task_id, role, description, patch))

    async def ingest(
        self,
        task_id: str,
        role: str,
        description: str,
        patch: str,
        project_id: str | None = None,
    ) -> int:
        """Chunk, embed, and upsert a patch. Returns number of chunks added.

        When project_id is provided, chunks go into the project-specific KB.
        After ingestion, the Promoter checks for cross-project patterns and
        promotes eligible chunks to the shared KB.
        """
        if not patch or len(patch.strip()) < 20:
            return 0

        source = f"patch:{task_id}"
        document = _PATCH_TEMPLATE.format(
            role=role,
            description=description[:300],
            patch=patch[:6000],
        )

        chunks: list[Chunk] = chunk_text(
            text=document,
            source=source,
            role=role,
        )
        if not chunks:
            return 0

        loop = asyncio.get_event_loop()
        vectors: list[list[float]] = await loop.run_in_executor(
            None,
            embed_texts,
            [c.text for c in chunks],
        )

        if len(vectors) != len(chunks):
            logger.warning(
                "[auto_ingest] Embedding count mismatch for task %s — skipping",
                task_id[:8],
            )
            return 0

        store = KnowledgeStore(role, project_id=project_id)

        # Entropy deduplication — drop near-duplicate chunks before upsert
        chunks, vectors = filter_by_entropy(chunks, vectors, store)
        if not chunks:
            logger.info(
                "[auto_ingest] All chunks from task %s were near-duplicates — skipped",
                task_id[:8],
            )
            return 0

        added = store.add_chunks(chunks, vectors)

        logger.info(
            "[auto_ingest] Ingested %d chunks from approved patch (task=%s role=%s project=%s)",
            added, task_id[:8], role, project_id or "global",
        )

        # Cross-project promotion — fire-and-forget, never blocks ingestion
        if project_id and _REDIS_URL:
            try:
                from registry.promoter import Promoter
                promoter = Promoter(_REDIS_URL)
                await promoter.check_and_promote(role, chunks, vectors, project_id)
            except Exception:
                logger.debug("[auto_ingest] Promoter failed", exc_info=True)

        return added
