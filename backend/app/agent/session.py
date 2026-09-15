"""Call lifecycle persistence: create the CALL row when a session starts, append
finalized CALL_MESSAGE rows as the conversation proceeds, and close the call out
when the session ends. Deliberately independent of any LiveKit types so it's
trivial to unit test."""

from __future__ import annotations

import logging
from datetime import datetime

from app.database.database import session_scope
from app.database.models import Call, CallMessage

logger = logging.getLogger(__name__)


def create_call(business_id: str, room_name: str | None = None) -> str:
    with session_scope() as session:
        call = Call(business_id=business_id, room_name=room_name, status="in_progress")
        session.add(call)
        session.flush()
        call_id = call.id
    logger.info("call started: id=%s business=%s room=%s", call_id, business_id, room_name)
    return call_id


def add_call_message(call_id: str, role: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    with session_scope() as session:
        session.add(CallMessage(call_id=call_id, role=role, text=text))


def end_call(call_id: str, status: str = "completed") -> None:
    with session_scope() as session:
        call = session.get(Call, call_id)
        if call is None:
            logger.warning("end_call: call %s not found", call_id)
            return
        call.ended_at = datetime.utcnow()
        call.status = status
        if call.started_at:
            call.duration_seconds = int((call.ended_at - call.started_at).total_seconds())
    logger.info("call ended: id=%s status=%s", call_id, status)
