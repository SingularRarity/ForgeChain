"""Anthropic Claude adapter."""

from __future__ import annotations

import os

import anthropic

from .base import BaseLLMProvider, LLMResponse


class AnthropicProvider(BaseLLMProvider):
    def __init__(self) -> None:
        api_key = os.environ["ANTHROPIC_API_KEY"]
        self._model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    def name(self) -> str:
        return "anthropic"

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
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        content = msg.content[0].text if msg.content else ""
        return LLMResponse(
            content=content,
            model=self._model,
            provider="anthropic",
            prompt_tokens=msg.usage.input_tokens,
            completion_tokens=msg.usage.output_tokens,
            raw=msg.model_dump(),
        )
