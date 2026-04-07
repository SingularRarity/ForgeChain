"""Cross-project pattern promotion.

After a patch is approved and ingested into a project's KB, the Promoter
checks whether any of the newly added chunks are also genuinely novel to
another active project's KB. If a pattern appears in 2+ projects with
high information gain in both, it is promoted to the shared KB — making
it available to every future project automatically.

Promotion criteria (both must pass):
  1. Similar chunk exists in at least one other project's KB
     (cosine similarity > CROSS_PROJECT_SIMILARITY_THRESHOLD, default 0.75)
  2. The chunk has high information gain against the shared KB
     (gain = 1 - max_similarity > SHARED_ENTROPY_THRESHOLD, default 0.5)

The higher thresholds (0.75 / 0.5 vs the ingestion threshold of 0.15)
ensure only genuinely reusable patterns reach the shared KB.
"""

from __future__ import annotations

import logging
import os

import sys
for _p in ["/packages", "../../packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from knowledge.store import KnowledgeStore
from knowledge.chunker import Chunk
from knowledge.embedder import embed_texts
from quant.entropy import should_ingest
from .project import ProjectRegistry, project_kb_path

logger = logging.getLogger(__name__)

# A chunk is considered a cross-project pattern if another project has
# a chunk THIS similar to it — meaning the same concept appears in both.
CROSS_PROJECT_SIMILARITY_THRESHOLD = float(
    os.getenv("FORGECHAIN_CROSS_PROJECT_SIMILARITY", "0.75")
)
# Minimum information gain against the shared KB before promoting.
# Higher than the normal ingestion threshold (0.15) to keep shared KB tight.
SHARED_ENTROPY_THRESHOLD = float(
    os.getenv("FORGECHAIN_SHARED_ENTROPY_THRESHOLD", "0.50")
)


class Promoter:
    """Detect and promote cross-project patterns to the shared knowledge base."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._registry = ProjectRegistry(redis_url)

    async def check_and_promote(
        self,
        role: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        source_project_id: str,
    ) -> int:
        """Check newly ingested chunks for cross-project patterns and promote if eligible.

        Returns the number of chunks promoted to the shared KB.
        """
        if source_project_id == "shared":
            return 0   # nothing to promote from shared to shared

        active_projects = await self._registry.list_active()
        other_project_ids = [
            p.project_id for p in active_projects
            if p.project_id != source_project_id
        ]

        if not other_project_ids:
            return 0   # no other projects to compare against

        shared_store = KnowledgeStore(role, project_id="shared")
        promoted = 0

        for chunk, vec in zip(chunks, vectors):
            if await self._is_cross_project_pattern(
                role, vec, source_project_id, other_project_ids
            ):
                if should_ingest(vec, shared_store, min_gain=SHARED_ENTROPY_THRESHOLD):
                    shared_store.add_chunks([chunk], [vec])
                    promoted += 1
                    logger.info(
                        "[promoter] Promoted chunk to shared KB (role=%s source=%r heading=%r)",
                        role, chunk.source, chunk.heading,
                    )

        if promoted:
            logger.info(
                "[promoter] %d chunk(s) promoted to shared/%s KB from project %r",
                promoted, role, source_project_id,
            )
        return promoted

    # ------------------------------------------------------------------

    async def _is_cross_project_pattern(
        self,
        role: str,
        vector: list[float],
        source_project_id: str,
        other_project_ids: list[str],
    ) -> bool:
        """Return True if any other project KB has a highly similar chunk."""
        for pid in other_project_ids:
            try:
                store = KnowledgeStore(role, project_id=pid)
                if store.count() == 0:
                    continue
                hits = store.query(vector, top_k=1, min_score=0.0)
                if hits and hits[0]["score"] >= CROSS_PROJECT_SIMILARITY_THRESHOLD:
                    logger.debug(
                        "[promoter] Cross-project match: %r ↔ %r (similarity=%.3f)",
                        source_project_id, pid, hits[0]["score"],
                    )
                    return True
            except Exception:
                logger.debug(
                    "[promoter] Could not query KB for project %r", pid, exc_info=True
                )
        return False
