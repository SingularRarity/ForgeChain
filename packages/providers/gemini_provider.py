"""Google Gemini adapter."""

from __future__ import annotations

import os

import google.generativeai as genai

from .base import BaseLLMProvider, LLMResponse


class GeminiProvider(BaseLLMProvider):
    def __init__(self) -> None:
        api_key = os.environ["GEMINI_API_KEY"]
        genai.configure(api_key=api_key)
        self._model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")
        self._client = genai.GenerativeModel(self._model_name)

    def name(self) -> str:
        return "gemini"

    def model_id(self) -> str:
        return self._model_name

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> LLMResponse:
        config = genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        prompt = f"{system}\n\n{user}"
        response = await self._client.generate_content_async(prompt, generation_config=config)
        content = response.text or ""
        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            content=content,
            model=self._model_name,
            provider="gemini",
            prompt_tokens=getattr(usage, "prompt_token_count", 0),
            completion_tokens=getattr(usage, "candidates_token_count", 0),
        )
