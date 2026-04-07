"""Text chunker — splits documents into embedding-sized pieces.

Strategy:
  - Prefer splitting on Markdown headings (##, ###) to keep
    conceptually related text together.
  - Fall back to paragraph breaks, then sentence breaks.
  - Hard cap at max_tokens * ~4 chars/token to fit nomic-embed-text
    (2048 token context window).

No external dependencies — pure Python.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Chunk:
    text: str
    source: str       # file path or URL
    role: str         # agent role this chunk belongs to
    heading: str      # nearest Markdown heading, or ""
    chunk_index: int


# nomic-embed-text supports 2048 tokens; ~4 chars/token → 512 chars safe minimum
_DEFAULT_MAX_CHARS = 1600   # ~400 tokens — leaves room for heading prefix
_DEFAULT_OVERLAP   = 200    # character overlap between consecutive chunks


def chunk_text(
    text: str,
    source: str,
    role: str,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
    overlap: int = _DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Split *text* into overlapping chunks, preserving Markdown heading context."""
    sections = _split_by_heading(text)
    chunks: list[Chunk] = []
    idx = 0

    for heading, body in sections:
        # Prefix body with its heading so each chunk is self-contained
        headed = f"{heading}\n{body}".strip() if heading else body.strip()
        for piece in _sliding_window(headed, max_chars, overlap):
            chunks.append(Chunk(
                text=piece,
                source=source,
                role=role,
                heading=heading,
                chunk_index=idx,
            ))
            idx += 1

    return chunks


def _split_by_heading(text: str) -> list[tuple[str, str]]:
    """Split on ## / ### headings, returning (heading, body) pairs."""
    pattern = re.compile(r"^(#{1,4}\s+.+)$", re.MULTILINE)
    parts: list[tuple[str, str]] = []
    pos = 0
    current_heading = ""

    for match in pattern.finditer(text):
        body = text[pos:match.start()].strip()
        if body:
            parts.append((current_heading, body))
        current_heading = match.group(1).strip()
        pos = match.end()

    remainder = text[pos:].strip()
    if remainder:
        parts.append((current_heading, remainder))

    return parts if parts else [("", text)]


def _sliding_window(text: str, max_chars: int, overlap: int) -> Iterator[str]:
    """Yield overlapping windows of *text*."""
    if len(text) <= max_chars:
        yield text
        return
    start = 0
    while start < len(text):
        end = start + max_chars
        yield text[start:end]
        start += max_chars - overlap
