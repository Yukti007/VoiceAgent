"""Concrete LLM providers + factory.

Three providers are implemented, selected via `LLM_PROVIDER` in `.env`:
- "openai": OpenAI's own API (needs OPENAI_API_KEY + billing).
- "sarvam": Sarvam's sarvam-105b model, over an OpenAI-compatible endpoint,
  using the SAME SARVAM_API_KEY already required for STT/TTS. NOTE: as of
  this writing Sarvam's Chat Completions API is beta-gated per-account --
  if you get a 400 "This endpoint is currently in beta" error, your account
  doesn't have access yet; use "groq" instead.
- "groq": Groq's free tier (console.groq.com, no card required), also over
  an OpenAI-compatible endpoint -- the most reliable free fallback.

Adding a fourth provider means adding one more class here and one more
branch in `get_llm_provider` -- nothing else in the app changes.
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


class SarvamLLMProvider(LLMProvider):
    """Sarvam's sarvam-105b model. OpenAI-compatible chat completions, so we
    can reuse the plain `openai` SDK for the extraction call by pointing it
    at Sarvam's base URL -- no new SDK dependency needed."""

    # Matches livekit.plugins.sarvam.llm.client._resolve_base_url("sarvam-105b"):
    # sarvam-105b-conversations uses /v1, every other model (incl. the default
    # sarvam-105b) uses /v2.
    _BASE_URL = "https://api.sarvam.ai/v2"

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = AsyncOpenAI(api_key=settings.sarvam_api_key, base_url=self._BASE_URL)

    def get_agent_llm(self):
        from livekit.plugins import sarvam as sarvam_plugin

        return sarvam_plugin.LLM(
            model=self._settings.sarvam_llm_model,
            api_key=self._settings.sarvam_api_key,
        )

    async def complete_json(self, *, system: str, user: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._settings.sarvam_llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content
        return content or "{}"


class GroqProvider(LLMProvider):
    """Groq's free-tier hosted Llama models. OpenAI-compatible endpoint, so
    both the realtime agent LLM and the extraction call reuse the same
    `openai.LLM`/`AsyncOpenAI` classes as OpenAIProvider, just pointed
    elsewhere -- no Groq-specific SDK needed."""

    _BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = AsyncOpenAI(api_key=settings.groq_api_key, base_url=self._BASE_URL)

    def get_agent_llm(self):
        from livekit.plugins import openai as lk_openai

        return lk_openai.LLM(
            model=self._settings.groq_model,
            api_key=self._settings.groq_api_key,
            base_url=self._BASE_URL,
        )

    async def complete_json(self, *, system: str, user: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._settings.groq_model,
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
    if provider == "sarvam":
        return SarvamLLMProvider(settings)
    if provider == "groq":
        return GroqProvider(settings)
    raise ValueError(
        f"Unsupported LLM_PROVIDER={settings.llm_provider!r}. "
        "Add a new LLMProvider implementation in app/llm/provider.py to support it."
    )
