"""Post-call structured extraction.

Runs once, after a call ends: feeds the full transcript to the LLM, asks for
strict JSON matching `CallExtraction`, validates it with Pydantic, and stores
it. Failures are logged and swallowed -- the raw transcript always survives
even if extraction breaks (bad JSON, LLM outage, etc.).
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError
from sqlalchemy import select

from app.database.database import session_scope
from app.database.models import Call, CallExtraction as CallExtractionRow, CallMessage
from app.llm.provider import get_llm_provider
from app.postcall.schemas import CallExtraction

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You are a data-extraction assistant for a dental clinic's call center.
You will be given a transcript of a phone call between a customer and an AI receptionist named Aisha.

Extract structured information and return ONLY a single JSON object (no markdown, no commentary)
with exactly these keys:

- intent: one of "appointment_booking", "price_inquiry", "hours_inquiry", "general_inquiry",
  "cancellation", "other"
- customer_name: string or null
- customer_phone: string or null (digits only)
- language: the primary language the customer used, e.g. "English", "Hindi", or "Hinglish"
- service: the dental service discussed/booked, or null
- appointment_date: "YYYY-MM-DD" or null (only if an appointment was actually booked or firmly agreed)
- appointment_time: "HH:MM" 24-hour format, or null
- outcome: one of "appointment_booked", "information_provided", "no_action", "escalation_needed", "other"
- requires_followup: boolean -- true if a human staff member should follow up
- summary: one or two sentence plain-English summary of the call

Only extract information that is actually present in the transcript. Do not guess or invent
a date, time, phone number, or name that was not mentioned."""


def _format_transcript(messages: list[CallMessage]) -> str:
    lines = []
    for m in messages:
        speaker = "Customer" if m.role == "user" else "Aisha"
        lines.append(f"{speaker}: {m.text}")
    return "\n".join(lines)


async def run_post_call_extraction(call_id: str) -> CallExtraction | None:
    """Extract + validate + persist. Returns the validated extraction, or None
    if extraction could not be completed (already logged)."""

    with session_scope() as session:
        call = session.get(Call, call_id)
        if call is None:
            logger.error("post-call extraction: call %s not found", call_id)
            return None

        messages = (
            session.execute(
                select(CallMessage).where(CallMessage.call_id == call_id).order_by(CallMessage.timestamp)
            )
            .scalars()
            .all()
        )
        transcript = _format_transcript(messages)

    if not transcript.strip():
        logger.warning("post-call extraction: call %s has no messages, skipping", call_id)
        return None

    try:
        provider = get_llm_provider()
        raw = await provider.complete_json(
            system=EXTRACTION_SYSTEM_PROMPT,
            user=f"Transcript:\n\n{transcript}",
        )
        data = json.loads(raw)
        extraction = CallExtraction.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.error("post-call extraction: invalid LLM output for call %s: %s", call_id, exc)
        return None
    except Exception as exc:  # LLM/network failure -- never crash the app over this
        logger.error("post-call extraction: LLM call failed for call %s: %s", call_id, exc)
        return None

    with session_scope() as session:
        call = session.get(Call, call_id)
        if call is None:
            return extraction

        existing = session.execute(
            select(CallExtractionRow).where(CallExtractionRow.call_id == call_id)
        ).scalar_one_or_none()
        if existing is None:
            existing = CallExtractionRow(call_id=call_id)
            session.add(existing)

        existing.intent = extraction.intent
        existing.customer_name = extraction.customer_name
        existing.customer_phone = extraction.customer_phone
        existing.language = extraction.language
        existing.service = extraction.service
        existing.appointment_date = extraction.appointment_date
        existing.appointment_time = extraction.appointment_time
        existing.outcome = extraction.outcome
        existing.requires_followup = extraction.requires_followup
        existing.summary = extraction.summary
        existing.structured_json = extraction.model_dump()

        call.summary = extraction.summary
        call.outcome = extraction.outcome
        call.detected_language = extraction.language

        logger.info("post-call extraction stored for call %s: outcome=%s", call_id, extraction.outcome)

    return extraction
