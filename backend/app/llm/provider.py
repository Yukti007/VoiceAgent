"""Concrete LLM providers + factory.

V0 implements exactly one provider (OpenAI), selected via `LLM_PROVIDER` in
`.env`. Adding a second provider later means adding one more class here and
one more branch in `get_llm_provider` -- nothing else in the app changes.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.config import Settings, get_settings
from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """Fast/inexpensive OpenAI model, suitable for realtime voice turn-taking."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    def get_agent_llm(self):
        from livekit.plugins import openai as lk_openai

        return lk_openai.LLM(
            model=self._settings.openai_model,
            api_key=self._settings.openai_api_key,
        )

    async def complete_json(self, *, system: str, user: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._settings.openai_extraction_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content
        return content or "{}"


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()
    if provider == "openai":
        return OpenAIProvider(settings)
    raise ValueError(
        f"Unsupported LLM_PROVIDER={settings.llm_provider!r}. "
        "Add a new LLMProvider implementation in app/llm/provider.py to support it."
    )
