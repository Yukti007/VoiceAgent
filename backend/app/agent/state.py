"""Per-session userdata threaded through AgentSession -> RunContext -> tools."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionData:
    business_id: str
    call_id: str
    room_name: str | None = None
    # Booking confirmation_key -> number of caller turns when its details were
    # read back. A booking commits only on a later caller turn (see
    # Assistant._book_after_read_back).
    read_backs: dict[str, int] = field(default_factory=dict)
