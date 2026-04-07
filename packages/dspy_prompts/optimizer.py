"""Offline DSPy prompt optimizer.

Run this script periodically (e.g. weekly CI job) to improve prompt quality
using examples gathered from the production ledger.

Usage:
    python -m dspy_prompts.optimizer --role backend_dev --tier mid --examples examples.jsonl

The optimised program is saved to $FORGECHAIN_WEIGHTS_DIR/{role}_{tier}.json
and loaded automatically by ForgeChainModule at runtime.

Optimizer used: MIPROv2 (best quality/cost balance).
For faster iterations use BootstrapFewShot (no LLM calls for optimisation).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

import dspy
from dspy.teleprompt import MIPROv2, BootstrapFewShot

from .signatures import get_signature
from .lm_config import configure_dspy_lm

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_WEIGHTS_DIR = Path(os.getenv("FORGECHAIN_WEIGHTS_DIR", "/app/dspy_weights"))


def load_examples(path: str, role: str, tier: str) -> list[dspy.Example]:
    """Load JSONL training examples — each line is a dict of input+output field values."""
    examples: list[dspy.Example] = []
    sig = get_signature(role, tier)  # type: ignore[arg-type]
    output_keys = [
        f for f in sig.model_fields
        if hasattr(sig.model_fields[f].default, "__class__")
        and sig.model_fields[f].default.__class__.__name__ == "OutputField"
    ]
    # Simpler: just treat all keys not in input fields as outputs
    input_keys = [k for k, v in sig.__annotations__.items() if "InputField" in repr(v)]

    with open(path) as f:
        for line in f:
            data: dict[str, Any] = json.loads(line.strip())
            ex = dspy.Example(**data).with_inputs(*input_keys)
            examples.append(ex)
    return examples


def confidence_metric(prediction: dspy.Prediction, example: dspy.Example) -> float:
    """Simple metric: high confidence = 1.0, medium = 0.5, low = 0.0.
    Override with domain-specific evaluation for better optimisation.
    """
    conf = getattr(prediction, "confidence", "low").strip().lower()
    return {"high": 1.0, "medium": 0.5, "low": 0.0}.get(conf, 0.0)


def optimize(
    role: str,
    tier: str,
    examples_path: str,
    *,
    fast: bool = False,
    max_bootstrapped_demos: int = 3,
    num_candidates: int = 10,
) -> None:
    configure_dspy_lm(tier)  # type: ignore[arg-type]

    sig = get_signature(role, tier)  # type: ignore[arg-type]
    program = dspy.ChainOfThought(sig)

    examples = load_examples(examples_path, role, tier)
    if not examples:
        logger.error("No examples loaded from %s — aborting", examples_path)
        return

    logger.info("Optimising %s/%s with %d examples (fast=%s)", role, tier, len(examples), fast)

    if fast:
        optimizer = BootstrapFewShot(
            metric=confidence_metric,
            max_bootstrapped_demos=max_bootstrapped_demos,
        )
    else:
        optimizer = MIPROv2(
            metric=confidence_metric,
            auto="medium",
            num_candidates=num_candidates,
        )

    optimised = optimizer.compile(program, trainset=examples)

    _WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _WEIGHTS_DIR / f"{role}_{tier}.json"
    optimised.save(str(out_path))
    logger.info("Saved optimised program to %s", out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Optimize ForgeChain DSPy prompts")
    parser.add_argument("--role",     required=True, help="Agent role, e.g. backend_dev")
    parser.add_argument("--tier",     required=True, help="Tier: junior|mid|senior|cto")
    parser.add_argument("--examples", required=True, help="Path to JSONL training examples")
    parser.add_argument("--fast",     action="store_true", help="Use BootstrapFewShot (no LLM calls)")
    parser.add_argument("--candidates", type=int, default=10, help="MIPROv2 num_candidates")
    args = parser.parse_args()

    optimize(
        args.role,
        args.tier,
        args.examples,
        fast=args.fast,
        num_candidates=args.candidates,
    )
