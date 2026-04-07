"""xAI Grok adapter (OpenAI-compatible API)."""

from __future__ import annotations

import os

from openai import AsyncOpenAI

from .base import BaseLLMProvider, LLMResponse

_GROK_BASE_URL = "https://api.x.ai/v1"


class GrokProvider(BaseLLMProvider):
    def __init__(self) -> None:
        api_key = os.environ["GROK_API_KEY"]
        self._model = os.getenv("GROK_MODEL", "grok-3-mini")
        self._client = AsyncOpenAI(api_key=api_key, base_url=_GROK_BASE_URL)

    def name(self) -> str:
        return "grok"

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
            provider="grok",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )
