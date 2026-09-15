"""Per-session userdata threaded through AgentSession -> RunContext -> tools."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionData:
    business_id: str
    call_id: str
    room_name: str | None = None
