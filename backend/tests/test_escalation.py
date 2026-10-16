"""Human hand-off: the callback request is stored, surfaces in the API, and
forces requires_followup on the post-call extraction regardless of what the
extraction LLM concluded."""

import json

from fastapi.testclient import TestClient

from app.agent.session import add_call_message, create_call
from app.database.database import session_scope
from app.database.models import CallExtraction, Escalation
from app.main import app
from app.postcall import extractor
from app.tools.escalation import request_human_callback


def _escalate(business_id: str, call_id: str | None, **overrides) -> dict:
    kwargs = dict(business_id=business_id, call_id=call_id, reason="Caller has a billing question")
    kwargs.update(overrides)
    with session_scope() as session:
        return request_human_callback(session, **kwargs)


def test_callback_is_stored_with_a_normalised_phone(business_id):
    call_id = create_call(business_id, room_name="escalation-room")
    result = _escalate(business_id, call_id, customer_phone="+91 98111 22333", urgent=True)
    assert result["success"] and result["urgent"] and result["has_phone"]
    with session_scope() as session:
        row = session.get(Escalation, result["escalation_id"])
        assert (row.call_id, row.customer_phone, row.status) == (call_id, "9811122333", "open")


def test_callback_rejects_an_unusable_phone_but_allows_none(business_id):
    assert _escalate(business_id, None, customer_phone="12")["success"] is False
    assert _escalate(business_id, None)["success"] is True
    assert _escalate(business_id, None, reason="  ")["success"] is False


def test_escalations_are_listed_by_the_api(business_id):
    _escalate(business_id, None, reason="Wants to speak to Dr. Mehta personally")
    with TestClient(app) as client:
        rows = client.get("/api/escalations", params={"business_id": business_id}).json()
        assert client.get("/api/health").json()["status"] == "ok"
    assert any(r["reason"] == "Wants to speak to Dr. Mehta personally" for r in rows)


class _FakeProvider:
    async def complete_json(self, *, system: str, user: str) -> str:
        return json.dumps(
            {
                "intent": "general_inquiry",
                "language": "English",
                "outcome": "information_provided",
                "requires_followup": False,
                "summary": "Caller asked about billing.",
            }
        )


async def test_raised_callback_forces_followup_on_the_extraction(business_id, monkeypatch):
    monkeypatch.setattr(extractor, "get_llm_provider", lambda: _FakeProvider())
    call_id = create_call(business_id, room_name="escalation-extraction")
    add_call_message(call_id, "user", "I was charged twice, can someone call me?")
    add_call_message(call_id, "assistant", "I'll ask a staff member to call you back.")
    _escalate(business_id, call_id)

    result = await extractor.run_post_call_extraction(call_id)

    assert result is not None and result.requires_followup is False  # the LLM's view
    with session_scope() as session:
        stored = session.query(CallExtraction).filter_by(call_id=call_id).one()
        assert stored.requires_followup is True
        assert stored.summary == "Caller asked about billing."
