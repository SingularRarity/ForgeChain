"""Embedder — converts text chunks to vectors via Ollama's embedding API.

Model: nomic-embed-text (768d)
  - Runs entirely inside Ollama — $0, no external API calls
  - 2048 token context window
  - Pull once: `ollama pull nomic-embed-text`

Alternative: mxbai-embed-large (1024d, slightly better recall, 2x RAM)
  Set FORGECHAIN_EMBED_MODEL=mxbai-embed-large

Both are free and local.
"""

from __future__ import annotations

import os
import time
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_OLLAMA_URL   = os.getenv("LOCAL_LLM_URL", "http://localhost:11434")
_EMBED_MODEL  = os.getenv("FORGECHAIN_EMBED_MODEL", "nomic-embed-text")
_BATCH_SIZE   = 16   # Ollama embedding endpoint handles one text at a time;
                     # we batch our HTTP calls with a small concurrency limit
_RETRY_LIMIT  = 3
_RETRY_DELAY  = 2.0  # seconds


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Return a list of embedding vectors, one per input text. Synchronous."""
    vectors: list[list[float]] = []
    for i, text in enumerate(texts):
        vec = _embed_one(text)
        vectors.append(vec)
        if (i + 1) % 50 == 0:
            logger.info("Embedded %d / %d chunks", i + 1, len(texts))
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a single query string for retrieval."""
    return _embed_one(text)


def _embed_one(text: str) -> list[float]:
    url = f"{_OLLAMA_URL}/api/embed"
    payload: dict[str, Any] = {"model": _EMBED_MODEL, "input": text}

    for attempt in range(_RETRY_LIMIT):
        try:
            resp = httpx.post(url, json=payload, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            # Ollama returns {"embeddings": [[...]]} for /api/embed
            embeddings = data.get("embeddings") or data.get("embedding")
            if isinstance(embeddings[0], list):
                return embeddings[0]
            return embeddings
        except Exception as e:
            if attempt < _RETRY_LIMIT - 1:
                logger.warning("Embedding attempt %d failed: %s — retrying", attempt + 1, e)
                time.sleep(_RETRY_DELAY)
            else:
                raise RuntimeError(
                    f"Ollama embedding failed after {_RETRY_LIMIT} attempts. "
                    f"Is Ollama running and is '{_EMBED_MODEL}' pulled?"
                ) from e
    return []  # unreachable
