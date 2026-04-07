"""Ollama / local LLM adapter (OpenAI-compatible)."""

from __future__ import annotations

import os

from openai import AsyncOpenAI

from .base import BaseLLMProvider, LLMResponse


class OllamaProvider(BaseLLMProvider):
    def __init__(self) -> None:
        base_url = os.getenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
        self._model = os.getenv("LOCAL_LLM_MODEL", "llama3.2")
        # Ollama doesn't require a real API key
        self._client = AsyncOpenAI(api_key="ollama", base_url=base_url)

    def name(self) -> str:
        return "ollama"

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
            provider="ollama",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )
