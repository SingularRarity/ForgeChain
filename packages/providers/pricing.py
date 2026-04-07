"""Token pricing table (USD per 1M tokens, as of 2026-Q1).

Update these figures when provider pricing changes.
Each entry: (input_cost_per_1m, output_cost_per_1m)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Tier = Literal["junior", "mid", "senior", "cto"]

# ── Provider → model → (input $/1M, output $/1M) ────────────────────────── #
_PRICES: dict[str, dict[str, tuple[float, float]]] = {
    "ollama": {
        # Local inference — only electricity cost, set to $0
        "forgechain-junior":     (0.0, 0.0),  # custom modelfile on qwen2.5-coder:7b
        "qwen2.5-coder:7b":      (0.0, 0.0),
        "qwen2.5-coder:14b":     (0.0, 0.0),
        "deepseek-coder-v2:16b": (0.0, 0.0),
        "codellama:13b":         (0.0, 0.0),
        "llama3.2":              (0.0, 0.0),  # fallback only
    },
    "grok": {
        "grok-3-mini":     (0.30,  0.50),
        "grok-3":          (3.00,  15.00),
    },
    "gemini": {
        "gemini-1.5-flash": (0.075, 0.30),
        "gemini-1.5-pro":   (3.50,  10.50),
        "gemini-2.0-flash": (0.10,  0.40),
    },
    "kilo": {
        # Free daily allowance — $0 until quota exhausted, then ~$0.50–$2/1M
        # Run GET https://api.kilo.ai/api/gateway/models for the live model list
        "qwen/qwen3-coder":             (0.0, 0.0),  # Qwen3 Coder — code-first, junior tier
        "deepseek/deepseek-r1-0528":    (0.0, 0.0),  # DeepSeek R1 — reasoning, mid tier
        "moonshotai/kimi-k2":           (0.0, 0.0),  # Kimi K2 — versatile
        "moonshotai/kimi-k2.5":         (0.0, 0.0),  # Kimi K2.5
        "minimax/minimax-m2":           (0.0, 0.0),  # MiniMax M2
        "z-ai/glm-4-7":                 (0.0, 0.0),  # GLM 4.7
        "z-ai/glm-4-5-air":             (0.0, 0.0),  # GLM 4.5 Air (lightweight)
        "arcee-ai/trinity-large":       (0.0, 0.0),  # Trinity Large Preview
    },
    "anthropic": {
        "claude-haiku-4-5-20251001": (0.80,   4.00),
        "claude-sonnet-4-6":         (3.00,  15.00),
        "claude-opus-4-6":           (15.00, 75.00),
    },
}


@dataclass(frozen=True)
class CallCost:
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    input_cost_usd: float
    output_cost_usd: float

    @property
    def total_cost_usd(self) -> float:
        return self.input_cost_usd + self.output_cost_usd

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "input_cost_usd": round(self.input_cost_usd, 8),
            "output_cost_usd": round(self.output_cost_usd, 8),
            "total_cost_usd": round(self.total_cost_usd, 8),
        }


def calculate_cost(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> CallCost:
    models = _PRICES.get(provider, {})
    # Fuzzy match: strip version suffix if exact key not found
    rate = models.get(model) or next(
        (v for k, v in models.items() if model.startswith(k) or k.startswith(model)),
        (0.001, 0.002),  # unknown model → conservative non-zero estimate
    )
    input_cost  = prompt_tokens     / 1_000_000 * rate[0]
    output_cost = completion_tokens / 1_000_000 * rate[1]
    return CallCost(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
    )


# ── Tier → (provider, model) default mapping ─────────────────────────────── #
TIER_DEFAULTS: dict[Tier, tuple[str, str]] = {
    "junior":  ("ollama",    "llama3.2"),
    "mid":     ("grok",      "grok-3-mini"),
    "senior":  ("gemini",    "gemini-1.5-pro"),
    "cto":     ("anthropic", "claude-sonnet-4-6"),
}
