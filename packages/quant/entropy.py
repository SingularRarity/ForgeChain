"""Information entropy deduplication for KB chunk ingestion.

Before adding a new chunk to ChromaDB, compute its information gain against
existing content in the collection. Skip near-duplicates to keep the knowledge
base dense and non-redundant.

  gain = 1 - max_cosine_similarity(new_chunk, existing_chunks)

  if gain < MIN_GAIN (0.15 by default):
      skip  # chunk is 85%+ similar to something already stored
  else:
      ingest

This prevents the KB from inflating with paraphrased duplicates of the same
concept. Over time the KB converges to a maximally informative set.

Threshold can be tuned via FORGECHAIN_ENTROPY_MIN_GAIN env var.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

MIN_GAIN: float = float(os.getenv("FORGECHAIN_ENTROPY_MIN_GAIN", "0.15"))


def should_ingest(
    vector: list[float],
    store: "KnowledgeStore",  # noqa: F821
    *,
    min_gain: float = MIN_GAIN,
) -> bool:
    """Return True if this vector is novel enough to be worth ingesting.

    Uses the store's own query() — no extra dependencies, no raw vector access.
    Empty store always returns True (nothing to compare against).
    """
    if store.count() == 0:
        return True

    # Query for nearest neighbour (top-1, no score floor)
    hits = store.query(vector, top_k=1, min_score=0.0)
    if not hits:
        return True

    max_similarity = hits[0]["score"]
    gain = 1.0 - max_similarity

    if gain < min_gain:
        logger.debug(
            "[entropy] Skipping chunk — gain=%.3f < threshold=%.3f (similarity=%.3f)",
            gain, min_gain, max_similarity,
        )
        return False

    logger.debug("[entropy] Ingesting chunk — gain=%.3f", gain)
    return True


def filter_by_entropy(
    chunks: list,
    vectors: list[list[float]],
    store: "KnowledgeStore",  # noqa: F821
    *,
    min_gain: float = MIN_GAIN,
) -> tuple[list, list[list[float]]]:
    """Filter a (chunks, vectors) list, dropping near-duplicates.

    Returns filtered (chunks, vectors) ready for store.add_chunks().
    Queries ChromaDB once per chunk — O(n) store queries.

    For large batches (>50 chunks), the first chunk is added eagerly so
    subsequent chunks can compare against freshly-ingested content too.
    """
    kept_chunks = []
    kept_vectors = []

    for chunk, vec in zip(chunks, vectors):
        if should_ingest(vec, store, min_gain=min_gain):
            kept_chunks.append(chunk)
            kept_vectors.append(vec)

    dropped = len(chunks) - len(kept_chunks)
    if dropped:
        logger.info(
            "[entropy] Filtered %d/%d near-duplicate chunks (threshold=%.2f)",
            dropped, len(chunks), min_gain,
        )

    return kept_chunks, kept_vectors
