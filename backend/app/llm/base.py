"""LLM provider abstraction.

Two things need an LLM in this app:
  1. The realtime voice agent's conversational brain (a `livekit.agents.llm.LLM`
     instance, streamed token-by-token into TTS).
  2. A one-shot, plain text-in/JSON-out call for post-call extraction.

`LLMProvider` covers both so swapping `LLM_PROVIDER` (or later adding
Anthropic/Gemini/a local model) never touches agent or post-call code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livekit.agents import llm as lk_llm


class LLMProvider(ABC):
    @abstractmethod
    def get_agent_llm(self) -> "lk_llm.LLM":
        """Return a LiveKit-Agents-compatible LLM instance for the realtime session."""
        ...

    @abstractmethod
    async def complete_json(self, *, system: str, user: str) -> str:
        """Run a single non-streaming completion constrained to return a JSON object
        (as raw text -- caller is responsible for `json.loads` + Pydantic validation)."""
        ...
