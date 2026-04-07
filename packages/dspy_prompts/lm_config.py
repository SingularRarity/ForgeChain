"""Configure DSPy's global LM for a given tier.

DSPy uses litellm under the hood, so all four providers map to litellm model strings.
Call `configure_dspy_lm(tier)` before running any DSPy module.
"""

from __future__ import annotations

import os
from typing import Literal

import dspy

Tier = Literal["junior", "mid", "senior", "cto"]

# litellm model strings per tier
_TIER_LM: dict[Tier, str] = {
    "junior":  "ollama/{model}",
    "mid":     "xai/{model}",
    "senior":  "gemini/{model}",
    "cto":     "anthropic/{model}",
}

_TIER_MODEL_ENV: dict[Tier, str] = {
    "junior":  "LOCAL_LLM_MODEL",
    "mid":     "GROK_MODEL",
    "senior":  "GEMINI_MODEL",
    "cto":     "ANTHROPIC_MODEL",
}

_TIER_KEY_ENV: dict[Tier, str | None] = {
    "junior":  None,                  # no key for local Ollama
    "mid":     "GROK_API_KEY",
    "senior":  "GEMINI_API_KEY",
    "cto":     "ANTHROPIC_API_KEY",
}

_TIER_DEFAULTS: dict[Tier, str] = {
    "junior": "llama3.2",
    "mid":    "grok-3-mini",
    "senior": "gemini-1.5-pro",
    "cto":    "claude-sonnet-4-6",
}


def configure_dspy_lm(tier: Tier, *, max_tokens: int = 4096, temperature: float = 0.2) -> dspy.LM:
    """Build and configure a DSPy LM for *tier*, set it as the global default, return it."""
    model_name = os.getenv(_TIER_MODEL_ENV[tier], _TIER_DEFAULTS[tier])
    lm_string = _TIER_LM[tier].format(model=model_name)

    kwargs: dict = {"max_tokens": max_tokens, "temperature": temperature}

    key_env = _TIER_KEY_ENV[tier]
    if key_env:
        api_key = os.environ.get(key_env, "")
        if api_key:
            kwargs["api_key"] = api_key

    # Ollama needs api_base
    if tier == "junior":
        kwargs["api_base"] = os.getenv("LOCAL_LLM_URL", "http://localhost:11434")

    lm = dspy.LM(lm_string, **kwargs)
    dspy.configure(lm=lm)
    return lm
