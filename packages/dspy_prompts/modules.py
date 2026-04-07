"""DSPy modules — wrap signatures in the right reasoning strategy per tier.

  junior  → Predict         (single forward pass, cheapest)
  mid     → ChainOfThought  (step-by-step reasoning, better accuracy)
  senior  → ChainOfThought  (richer chain, more tokens)
  cto     → ChainOfThought  (with explicit self-critique loop)

Optimised prompt weights are loaded from disk if available
(written by the offline optimizer — see optimizer.py).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

import dspy

from .signatures import get_signature, CTOReviewSignature
from .lm_config import configure_dspy_lm

logger = logging.getLogger(__name__)

Tier = Literal["junior", "mid", "senior", "cto"]

# Where optimized compiled program weights are stored
_WEIGHTS_DIR = Path(os.getenv("FORGECHAIN_WEIGHTS_DIR", "/app/dspy_weights"))


def _load_compiled(role: str, tier: str) -> dspy.Module | None:
    """Return a compiled (optimized) DSPy program if weights exist on disk."""
    path = _WEIGHTS_DIR / f"{role}_{tier}.json"
    if not path.exists():
        return None
    try:
        sig = get_signature(role, tier)  # type: ignore[arg-type]
        module = dspy.ChainOfThought(sig)
        module.load(str(path))
        logger.info("Loaded compiled DSPy weights from %s", path)
        return module
    except Exception:
        logger.warning("Failed to load compiled weights from %s", path, exc_info=True)
        return None


class ForgeChainModule:
    """Unified entry point: configure LM, build the right module, run forward pass."""

    def __init__(self, role: str, tier: Tier) -> None:
        self.role = role
        self.tier = tier
        self._sig = get_signature(role, tier)

    def _build_module(self) -> dspy.Module:
        # Try loading pre-optimised weights first
        cached = _load_compiled(self.role, self.tier)
        if cached:
            return cached

        # ChainOfThought at every tier — local models especially need
        # explicit reasoning steps to produce coherent structured output.
        return dspy.ChainOfThought(self._sig)

    def run(self, **inputs: Any) -> dspy.Prediction:
        """Configure DSPy LM for this tier and run the module."""
        configure_dspy_lm(self.tier)
        module = self._build_module()
        return module(**inputs)

    def run_cto_review(
        self,
        task_description: str,
        senior_patch: str,
        senior_notes: str,
    ) -> dspy.Prediction:
        """Explicit CTO review — always uses CTOReviewSignature regardless of role."""
        configure_dspy_lm("cto")
        module = dspy.ChainOfThought(CTOReviewSignature)
        return module(
            task_description=task_description,
            senior_patch=senior_patch,
            senior_notes=senior_notes,
        )
