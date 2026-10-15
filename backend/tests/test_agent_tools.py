"""Tool-level behaviour of the agent's booking tools, with a fake RunContext
(no LiveKit room needed)."""

from contextlib import asynccontextmanager

import pytest

from app.agent import agent as agent_module
from app.agent.agent import BOOKING_FILLER, Assistant
from app.agent.state import SessionData
from app.database.database import session_scope
from app.database.models import Appointment


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


@pytest.fixture
def caller_turns(monkeypatch):
    """Control how many times the caller has spoken, as the chat context would."""
    turns = {"n": 1}
    monkeypatch.setattr(agent_module, "_caller_turns", lambda agent: turns["n"])
    return turns


def _open_slot(business_id: str) -> tuple[str, dict]:
    from app.tools.appointments import check_availability
    from tests.test_appointments import _first_weekday_with_availability

    date = _first_weekday_with_availability(business_id)
    with session_scope() as session:
        return date, check_availability(session, business_id=business_id, date=date)["available"][0]


def _book(agent: Assistant, ctx: FakeContext, **kwargs):
    return Assistant.book_appointment(agent, ctx, **kwargs)


async def test_booking_needs_a_read_back_and_a_later_caller_turn(business_id, caller_turns):
    date, slot = _open_slot(business_id)
    agent = Assistant(instructions="test")
    ctx = FakeContext(business_id)
    details = dict(
        customer_name="Tool Patient",
        customer_phone="9000000123",
        date=date,
        time=slot["time"],
        service="Consultation",
        doctor=slot["doctor"],
    )

    first = await _book(agent, ctx, **details)
    assert first["requires_confirmation"] is True
    assert first["read_back"]["phone_digits"] == "9 0 0 0 0 0 0 1 2 3"
    assert "confirmation_key" not in first

    # Calling again in the same turn (the caller hasn't answered) still books nothing.
    again = await _book(agent, ctx, **details)
    assert again["requires_confirmation"] is True
    assert ctx.interruptions_disallowed is False

    caller_turns["n"] = 2  # the caller said "yes"
    booked = await _book(agent, ctx, **details)
    assert booked["success"] is True
    assert ctx.interruptions_disallowed is True
    assert ctx.fillers == [BOOKING_FILLER]
    with session_scope() as session:
        assert session.get(Appointment, booked["appointment_id"]) is not None


async def test_changed_details_need_a_fresh_read_back(business_id, caller_turns):
    date, slot = _open_slot(business_id)
    agent = Assistant(instructions="test")
    ctx = FakeContext(business_id)
    details = dict(customer_name="Read Back", date=date, time=slot["time"], service="Consultation")

    await _book(agent, ctx, customer_phone="9000000125", **details)
    caller_turns["n"] = 2  # caller corrects the number
    corrected = await _book(agent, ctx, customer_phone="9000000126", **details)
    assert corrected["requires_confirmation"] is True


async def test_invalid_details_fail_before_any_read_back(business_id, caller_turns):
    agent = Assistant(instructions="test")
    ctx = FakeContext(business_id)
    result = await _book(
        agent, ctx, customer_name="X", customer_phone="123", date="2099-01-01", time="10:00", service="Consultation"
    )
    assert result["success"] is False and "requires_confirmation" not in result


async def test_booking_is_skipped_if_caller_already_interrupted(business_id, caller_turns):
    date, slot = _open_slot(business_id)
    agent = Assistant(instructions="test")
    ctx = FakeContext(business_id, already_interrupted=True)
    details = dict(
        customer_name="Nobody", customer_phone="9000000124", date=date, time=slot["time"], service="Consultation"
    )
    await _book(agent, ctx, **details)
    caller_turns["n"] = 2
    result = await _book(agent, ctx, **details)
    assert result["success"] is False
    assert "interrupted" in result["error"]
