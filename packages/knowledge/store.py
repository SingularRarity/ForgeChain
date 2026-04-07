"""ChromaDB-backed vector store.

One ChromaDB collection per agent role (e.g. "forgechain_backend_dev").
The collection is created on first access and persisted to disk at
FORGECHAIN_KB_PATH (default: /app/knowledge_base).

No server needed — ChromaDB runs in-process and writes to a local
directory. Production can swap to Qdrant (see docker-compose.prod.yml).
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

import chromadb
from chromadb.config import Settings

from .chunker import Chunk

logger = logging.getLogger(__name__)

_KB_PATH     = os.getenv("FORGECHAIN_KB_PATH", "/app/knowledge_base")
_COLLECTION_PREFIX = "forgechain_"


def _client() -> chromadb.ClientAPI:
    """Return a persistent ChromaDB client (singleton per process)."""
    return chromadb.PersistentClient(
        path=_KB_PATH,
        settings=Settings(anonymized_telemetry=False),
    )


def _collection_name(role: str) -> str:
    # ChromaDB collection names must be 3-63 chars, alphanumeric + underscores
    return f"{_COLLECTION_PREFIX}{role}"[:63]


class KnowledgeStore:
    """Add chunks and query them by role."""

    def __init__(self, role: str) -> None:
        self.role = role
        self._col = _client().get_or_create_collection(
            name=_collection_name(role),
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------ #
    # Write                                                                #
    # ------------------------------------------------------------------ #

    def add_chunks(self, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        """Upsert chunks into the collection. Returns count added."""
        if not chunks:
            return 0

        ids        = [_chunk_id(c) for c in chunks]
        documents  = [c.text for c in chunks]
        metadatas  = [
            {"source": c.source, "heading": c.heading, "role": c.role}
            for c in chunks
        ]

        # Upsert in batches of 100 (ChromaDB default limit)
        batch = 100
        added = 0
        for i in range(0, len(ids), batch):
            self._col.upsert(
                ids=ids[i:i+batch],
                documents=documents[i:i+batch],
                embeddings=vectors[i:i+batch],
                metadatas=metadatas[i:i+batch],
            )
            added += len(ids[i:i+batch])

        logger.info("[store:%s] Upserted %d chunks", self.role, added)
        return added

    def delete_source(self, source: str) -> None:
        """Remove all chunks that came from a given file or URL."""
        self._col.delete(where={"source": source})
        logger.info("[store:%s] Deleted chunks from source: %s", self.role, source)

    # ------------------------------------------------------------------ #
    # Read                                                                 #
    # ------------------------------------------------------------------ #

    def query(
        self,
        query_vector: list[float],
        *,
        top_k: int = 5,
        min_score: float = 0.25,  # cosine similarity threshold
    ) -> list[dict[str, Any]]:
        """Return top-k most relevant chunks above min_score."""
        if self._col.count() == 0:
            return []

        results = self._col.query(
            query_embeddings=[query_vector],
            n_results=min(top_k, self._col.count()),
            include=["documents", "metadatas", "distances"],
        )

        hits = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            score = 1.0 - dist  # cosine distance → similarity
            if score >= min_score:
                hits.append({"text": doc, "source": meta.get("source", ""),
                             "heading": meta.get("heading", ""), "score": score})

        return hits

    def count(self) -> int:
        return self._col.count()

    def list_sources(self) -> list[str]:
        """Return unique source paths/URLs in this collection."""
        if self._col.count() == 0:
            return []
        all_meta = self._col.get(include=["metadatas"])["metadatas"]
        return sorted({m.get("source", "") for m in all_meta if m})


def _chunk_id(chunk: Chunk) -> str:
    """Stable ID so re-ingesting the same content is idempotent."""
    raw = f"{chunk.source}:{chunk.chunk_index}:{chunk.text[:64]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
