"""LLM Provider factory and registry."""

from __future__ import annotations

import os
from typing import Literal

from .base import BaseLLMProvider, LLMResponse
from .anthropic_provider import AnthropicProvider
from .gemini_provider import GeminiProvider
from .grok_provider import GrokProvider
from .ollama_provider import OllamaProvider

ProviderName = Literal["anthropic", "gemini", "grok", "ollama"]

_REGISTRY: dict[str, type[BaseLLMProvider]] = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "grok": GrokProvider,
    "ollama": OllamaProvider,
}


def get_provider(name: ProviderName | None = None) -> BaseLLMProvider:
    """Factory: return a configured provider instance.

    Priority order when *name* is None:
      1. FORGECHAIN_LLM_PROVIDER env var
      2. anthropic (if ANTHROPIC_API_KEY present)
      3. gemini  (if GEMINI_API_KEY present)
      4. grok    (if GROK_API_KEY present)
      5. ollama  (always available as fallback)
    """
    if name is None:
        name = os.getenv("FORGECHAIN_LLM_PROVIDER")  # type: ignore[assignment]
    if name is None:
        for candidate, key_env in [
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("gemini", "GEMINI_API_KEY"),
            ("grok", "GROK_API_KEY"),
        ]:
            if os.getenv(key_env):
                name = candidate  # type: ignore[assignment]
                break
        else:
            name = "ollama"

    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown LLM provider: {name!r}. Choose from {list(_REGISTRY)}")
    return cls()


__all__ = [
    "BaseLLMProvider",
    "LLMResponse",
    "AnthropicProvider",
    "GeminiProvider",
    "GrokProvider",
    "OllamaProvider",
    "get_provider",
    "ProviderName",
]
