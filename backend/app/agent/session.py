"""Call lifecycle persistence: create the CALL row when a session starts, append
finalized CALL_MESSAGE rows as the conversation proceeds, and close the call out
when the session ends. Deliberately independent of any LiveKit types so it's
trivial to unit test.

The plain functions here are synchronous (SQLAlchemy + SQLite). The agent
worker must never call them directly on its event loop -- that loop also
drives audio, VAD and turn detection, and a blocking DB write there shows up
as stuttering audio and late interruptions. Use `CallMessageWriter` (or
`asyncio.to_thread`) from async code instead.
"""

from __future__ import annotations

import asyncio
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


def add_call_message(
    call_id: str, role: str, text: str, timestamp: datetime | None = None
) -> None:
    text = (text or "").strip()
    if not text:
        return
    with session_scope() as session:
        session.add(
            CallMessage(
                call_id=call_id,
                role=role,
                text=text,
                timestamp=timestamp or datetime.utcnow(),
            )
        )


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


class CallMessageWriter:
    """Persists transcript messages off the event loop, in arrival order.

    `enqueue()` is a cheap, non-blocking call that's safe from synchronous
    LiveKit event callbacks. A single background task drains the queue and
    runs each insert in a worker thread, so writes never block the audio loop
    and never reorder (the timestamp is captured at enqueue time, not at
    write time). Call `aclose()` before ending the call to flush what's left.
    """

    def __init__(self, call_id: str) -> None:
        self._call_id = call_id
        self._queue: asyncio.Queue[tuple[str, str, datetime] | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._run(), name=f"call-message-writer-{call_id}")

    def enqueue(self, role: str, text: str) -> None:
        if not (text or "").strip():
            return
        self._queue.put_nowait((role, text, datetime.utcnow()))

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            role, text, timestamp = item
            try:
                await asyncio.to_thread(add_call_message, self._call_id, role, text, timestamp)
            except Exception:
                # A lost transcript line must never take the call down with it.
                logger.exception("failed to persist %s message for call %s", role, self._call_id)

    async def aclose(self) -> None:
        self._queue.put_nowait(None)
        await self._task
