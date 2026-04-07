"""Kilo AI provider (OpenAI-compatible gateway).

Kilo offers a free daily allowance of requests to several capable models:
  - qwen/qwen3-coder              Qwen3 Coder (code-first, strong at junior tasks)
  - deepseek/deepseek-r1-0528     DeepSeek R1 (strong reasoning, good for mid tier)
  - moonshotai/kimi-k2            Kimi K2 (versatile, good throughput)
  - minimax/minimax-m2            MiniMax M2 (solid baseline)
  - z-ai/glm-4-7                  GLM 4.7 (lightweight)

Rate limits (free tier): 200 requests / hour per IP address.

Endpoint: https://api.kilo.ai/api/gateway
Auth:     Bearer KILO_API_KEY  (obtain from https://kilo.ai/profile)
"""

from __future__ import annotations

import os

from openai import AsyncOpenAI

from .base import BaseLLMProvider, LLMResponse

_BASE_URL = "https://api.kilo.ai/api/gateway"


class KiloProvider(BaseLLMProvider):
    def __init__(self) -> None:
        api_key = os.environ["KILO_API_KEY"]
        self._model = os.getenv("KILO_MODEL", "qwen/qwen3-coder")
        self._client = AsyncOpenAI(api_key=api_key, base_url=_BASE_URL)

    def name(self) -> str:
        return "kilo"

    def model_id(self) -> str:
        return self._model

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content or ""
        usage = response.usage
        return LLMResponse(
            content=content,
            model=self._model,
            provider="kilo",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )
