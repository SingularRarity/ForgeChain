"""Tiered LLM router.

Maps (role, complexity) → Tier → (provider, model).

Tiers:
  junior  → Ollama       — basic scaffolding, summarisation, simple CRUD
  mid     → Grok         — component logic, API design, integration tests
  senior  → Gemini       — complex patterns, performance, advanced SQL, ML
  cto     → Anthropic    — architectural review, refactoring, security gate

Role baseline tiers (starting point before escalation):

  Role            junior  mid     senior  cto
  ─────────────────────────────────────────────
  ba              ✓
  qa_backend      ✓
  frontend_dev            ✓
  backend_dev             ✓
  sre                     ✓
  db_eng                          ✓
  ai_eng                          ✓
  *cto_review                             ✓   ← explicit stage, any role

Escalation rules:
  - Workers self-assess output quality (DSPy confidence score < threshold).
  - One tier escalation per assessment cycle.
  - cto tier is ONLY reachable via explicit `cto_review` stage flag — never auto-escalated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .pricing import Tier, TIER_DEFAULTS
from .base import BaseLLMProvider


# ── Role → base tier ────────────────────────────────────────────────────── #

ROLE_BASE_TIER: dict[str, Tier] = {
    "ba":           "junior",
    "qa_backend":   "junior",
    "frontend_dev": "mid",
    "backend_dev":  "mid",
    "sre":          "mid",
    "db_eng":       "senior",
    "ai_eng":       "senior",
}

# Maximum auto-escalation ceiling (cto is manual only)
ROLE_MAX_AUTO_TIER: dict[str, Tier] = {
    "ba":           "mid",
    "qa_backend":   "senior",
    "frontend_dev": "senior",
    "backend_dev":  "senior",
    "sre":          "senior",
    "db_eng":       "senior",
    "ai_eng":       "senior",
}

_TIER_ORDER: list[Tier] = ["junior", "mid", "senior", "cto"]


def escalate(current: Tier, role: str, *, force_cto: bool = False) -> Tier:
    """Return next tier up, respecting the role's auto-escalation ceiling."""
    if force_cto:
        return "cto"
    ceiling: Tier = ROLE_MAX_AUTO_TIER.get(role, "senior")
    current_idx = _TIER_ORDER.index(current)
    ceiling_idx = _TIER_ORDER.index(ceiling)
    next_idx = min(current_idx + 1, ceiling_idx)
    return _TIER_ORDER[next_idx]


@dataclass(frozen=True)
class TierRoute:
    tier: Tier
    provider: str
    model: str


def get_route(role: str, tier: Optional[Tier] = None) -> TierRoute:
    """Resolve (role, tier?) → TierRoute with provider + model."""
    resolved_tier: Tier = tier or ROLE_BASE_TIER.get(role, "mid")
    # Allow env-var overrides per tier: FORGECHAIN_JUNIOR_MODEL=codellama etc.
    env_prefix = f"FORGECHAIN_{resolved_tier.upper()}"
    default_provider, default_model = TIER_DEFAULTS[resolved_tier]
    provider = os.getenv(f"{env_prefix}_PROVIDER", default_provider)
    model    = os.getenv(f"{env_prefix}_MODEL",    default_model)
    return TierRoute(tier=resolved_tier, provider=provider, model=model)


# ── Provider instantiation per route ────────────────────────────────────── #

def build_provider(route: TierRoute) -> BaseLLMProvider:
    """Instantiate the correct provider adapter for a given route."""
    from . import _REGISTRY
    cls = _REGISTRY.get(route.provider)
    if cls is None:
        raise ValueError(f"No adapter registered for provider '{route.provider}'")
    # Temporarily override model via env so the adapter picks it up
    import os
    model_key = {
        "anthropic": "ANTHROPIC_MODEL",
        "gemini":    "GEMINI_MODEL",
        "grok":      "GROK_MODEL",
        "ollama":    "LOCAL_LLM_MODEL",
    }.get(route.provider, "")
    old = os.environ.get(model_key)
    os.environ[model_key] = route.model
    try:
        return cls()
    finally:
        if old is None:
            os.environ.pop(model_key, None)
        else:
            os.environ[model_key] = old
