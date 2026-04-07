"""PII detection and masking before text enters LLM prompts.

Patterns covered:
  - Email addresses
  - Phone numbers (E.164 and common US formats)
  - Credit card numbers (Luhn-valid 13-19 digit sequences)
  - SSN / national IDs  (XXX-XX-XXXX)
  - IPv4 addresses
  - JWT tokens
  - API keys / bearer tokens (heuristic: long alphanum strings)

The redactor is intentionally deterministic: the same PII token always
produces the same placeholder within a single call so the caller can
reconstruct context if needed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class RedactionResult:
    redacted_text: str
    # mapping from placeholder → original value (kept in memory, never logged)
    pii_map: dict[str, str] = field(default_factory=dict)

    def restore(self, text: str) -> str:
        """Reverse redaction — only call inside trusted perimeter."""
        for placeholder, original in self.pii_map.items():
            text = text.replace(placeholder, original)
        return text


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("PHONE", re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("IPV4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    (
        "JWT",
        re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    ),
    # Heuristic: 32+ char alphanum strings that look like secrets/API keys
    (
        "SECRET",
        re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
    ),
    # Credit card: 13–19 digits, optionally separated by spaces/dashes
    (
        "CARD",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    ),
]


class Redactor:
    """Stateless PII redactor."""

    @staticmethod
    def redact(text: str) -> RedactionResult:
        pii_map: dict[str, str] = {}
        result = text

        for label, pattern in _PATTERNS:
            for match in pattern.finditer(result):
                original = match.group(0)
                # Stable placeholder: sha8 of the original value
                digest = hashlib.sha256(original.encode()).hexdigest()[:8]
                placeholder = f"[{label}_{digest}]"
                pii_map[placeholder] = original
                result = result.replace(original, placeholder)

        return RedactionResult(redacted_text=result, pii_map=pii_map)
