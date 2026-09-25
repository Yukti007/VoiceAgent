"""Tool-level behaviour of the agent's booking tool, with a fake RunContext
(no LiveKit room needed)."""

from contextlib import asynccontextmanager

from app.agent.agent import BOOKING_FILLER, Assistant
from app.agent.state import SessionData


class FakeContext:
    def __init__(self, business_id: str, *, already_interrupted: bool = False):
        self.userdata = SessionData(business_id=business_id, call_id="test-call")
        self.already_interrupted = already_interrupted
        self.interruptions_disallowed = False
        self.fillers: list[str] = []

    def disallow_interruptions(self):
        if self.already_interrupted:
            raise RuntimeError("speech already interrupted")
        self.interruptions_disallowed = True

    @asynccontextmanager
    async def with_filler(self, source, *, delay=0):
        self.fillers.append(source)
        yield


def _book(agent: Assistant, ctx: FakeContext, **kwargs):
    return Assistant.book_appointment(agent, ctx, **kwargs)


async def test_booking_disallows_interruptions_and_uses_filler(business_id):
    from tests.test_appointments import _first_weekday_with_availability
    from app.database.database import session_scope
    from app.tools.appointments import check_availability

    date = _first_weekday_with_availability(business_id)
    with session_scope() as session:
        slot = check_availability(session, business_id=business_id, date=date)["available"][0]

    agent = Assistant(instructions="test")
    ctx = FakeContext(business_id)
    result = await _book(
        agent,
        ctx,
        customer_name="Tool Patient",
        customer_phone="9000000123",
        date=date,
        time=slot["time"],
        service="Consultation",
        doctor=slot["doctor"],
    )
    assert result["success"] is True
    assert ctx.interruptions_disallowed is True
    assert ctx.fillers == [BOOKING_FILLER]


async def test_booking_is_skipped_if_caller_already_interrupted(business_id):
    agent = Assistant(instructions="test")
    ctx = FakeContext(business_id, already_interrupted=True)
    result = await _book(
        agent,
        ctx,
        customer_name="Nobody",
        customer_phone="9000000124",
        date="2099-01-01",
        time="10:00",
        service="Consultation",
    )
    assert result["success"] is False
    assert "interrupted" in result["error"]
