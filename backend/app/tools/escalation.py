"""Handing a call off to a human: the agent records a callback request that
staff work through, instead of improvising an answer it isn't sure of."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.database.models import Escalation
from app.tools.customer import InvalidPhoneError, normalize_phone


def request_human_callback(
    session: Session,
    *,
    business_id: str,
    call_id: str | None,
    reason: str,
    customer_phone: str | None = None,
    customer_name: str | None = None,
    urgent: bool = False,
) -> dict:
    """Record a callback request. The phone is optional (a caller may decline
    to give it; staff can still read the call transcript), but if one is given
    it must be a valid number so staff can actually call back."""
    if not reason.strip():
        return {"success": False, "error": "Say briefly why a staff member needs to call back."}
    phone = None
    if customer_phone and customer_phone.strip():
        try:
            phone = normalize_phone(customer_phone)
        except InvalidPhoneError as exc:
            return {"success": False, "error": str(exc)}

    escalation = Escalation(
        business_id=business_id,
        call_id=call_id,
        reason=reason.strip(),
        customer_name=(customer_name or "").strip() or None,
        customer_phone=phone,
        urgent=urgent,
    )
    session.add(escalation)
    session.flush()
    return {"success": True, "escalation_id": escalation.id, "urgent": urgent, "has_phone": phone is not None}
