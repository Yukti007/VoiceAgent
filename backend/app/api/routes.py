"""HTTP API used by the demo frontend.

Nothing here does realtime audio -- that's the LiveKit Agents worker
(app/agent/agent.py). This API only: (1) mints LiveKit room tokens so the
browser can connect, and (2) exposes calls/transcripts/appointments/extractions
for the demo UI's "call result" panel and for debugging.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from livekit import api as lk_api
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database.database import get_db
from app.database.models import (
    Appointment,
    Business,
    Call,
    CallExtraction,
    CallMessage,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ---- Schemas -----------------------------------------------------------------


class TokenRequest(BaseModel):
    business_id: str | None = None
    identity: str | None = None
    room_name: str | None = None


class TokenResponse(BaseModel):
    token: str
    url: str
    room_name: str
    identity: str


class BusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    agent_name: str
    description: str
    default_language: str
    supported_languages: list[str]
    greeting: str


class CallMessageOut(BaseModel):
    role: str
    text: str
    timestamp: datetime


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    business_id: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    status: str
    detected_language: str | None
    outcome: str | None
    summary: str | None


class CallDetailOut(CallOut):
    messages: list[CallMessageOut]


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    intent: str | None
    customer_name: str | None
    customer_phone: str | None
    language: str | None
    service: str | None
    appointment_date: str | None
    appointment_time: str | None
    outcome: str | None
    requires_followup: bool
    summary: str | None


class AppointmentOut(BaseModel):
    id: str
    customer_name: str
    date: str
    time: str
    service: str
    doctor: str | None
    status: str


# ---- Routes --------------------------------------------------------------


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {"status": "ok", "missing_credentials": settings.missing_credentials()}


@router.get("/business/{business_id}", response_model=BusinessOut)
def get_business(business_id: str, db: Session = Depends(get_db)) -> Business:
    business = db.get(Business, business_id)
    if business is None:
        raise HTTPException(status_code=404, detail=f"Business '{business_id}' not found")
    return business


@router.post("/token", response_model=TokenResponse)
def create_token(payload: TokenRequest, settings: Settings = Depends(get_settings)) -> TokenResponse:
    missing = set(settings.missing_credentials())
    if missing & {"LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}:
        raise HTTPException(
            status_code=503,
            detail=(
                "LiveKit credentials are not configured. "
                "Add LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET to backend/.env."
            ),
        )

    room_name = payload.room_name or f"sharma-dental-{uuid.uuid4().hex[:8]}"
    identity = payload.identity or f"caller-{uuid.uuid4().hex[:6]}"

    token = (
        lk_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            lk_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
    )

    return TokenResponse(token=token.to_jwt(), url=settings.livekit_url, room_name=room_name, identity=identity)


@router.get("/calls", response_model=list[CallOut])
def list_calls(
    business_id: str | None = None,
    room_name: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> list[Call]:
    query = select(Call).order_by(Call.started_at.desc()).limit(limit)
    if business_id:
        query = query.where(Call.business_id == business_id)
    if room_name:
        query = query.where(Call.room_name == room_name)
    return list(db.execute(query).scalars().all())


@router.get("/calls/{call_id}", response_model=CallDetailOut)
def get_call(call_id: str, db: Session = Depends(get_db)) -> CallDetailOut:
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail=f"Call '{call_id}' not found")
    return CallDetailOut(
        **CallOut.model_validate(call).model_dump(),
        messages=[
            CallMessageOut(role=m.role, text=m.text, timestamp=m.timestamp) for m in call.messages
        ],
    )


@router.get("/calls/{call_id}/extraction", response_model=ExtractionOut)
def get_call_extraction(call_id: str, db: Session = Depends(get_db)) -> CallExtraction:
    extraction = db.execute(
        select(CallExtraction).where(CallExtraction.call_id == call_id)
    ).scalar_one_or_none()
    if extraction is None:
        raise HTTPException(status_code=404, detail="Extraction not available yet for this call")
    return extraction


@router.get("/appointments", response_model=list[AppointmentOut])
def list_appointments(
    business_id: str | None = None, limit: int = 50, db: Session = Depends(get_db)
) -> list[dict]:
    query = select(Appointment).order_by(Appointment.created_at.desc()).limit(limit)
    if business_id:
        query = query.where(Appointment.business_id == business_id)
    appointments = db.execute(query).scalars().all()
    return [
        AppointmentOut(
            id=a.id,
            customer_name=a.customer.name,
            date=a.date,
            time=a.time,
            service=a.service,
            doctor=a.doctor,
            status=a.status,
        )
        for a in appointments
    ]
