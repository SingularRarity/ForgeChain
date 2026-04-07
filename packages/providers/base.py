"""Abstract base for all LLM provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class BaseLLMProvider(ABC):
    """All providers implement this interface."""

    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Send a completion request and return a structured response."""

    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    @abstractmethod
    def model_id(self) -> str:
        """Model identifier string."""
