"""SQLAlchemy ORM models for the V0 voice receptionist.

Kept intentionally relational + simple (SQLite-friendly). The migration path to
PostgreSQL is a `DATABASE_URL` change plus (eventually) Alembic migrations —
no model rewrites should be required.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Business(Base):
    """A tenant business. V0 runs a single seeded business, but the schema is
    already multi-tenant shaped (business_id foreign keys everywhere)."""

    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    default_language: Mapped[str] = mapped_column(String(20), default="en-IN")
    supported_languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    greeting: Mapped[str] = mapped_column(Text, default="")
    system_instructions: Mapped[str] = mapped_column(Text, default="")
    # Structured business knowledge: hours, services, doctors, policies, insurers.
    # V0: hand-authored JSON. Future: replaced/augmented by a RAG KnowledgeProvider.
    business_knowledge: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    calls: Mapped[list["Call"]] = relationship(back_populates="business")
    customers: Mapped[list["Customer"]] = relationship(back_populates="business")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="business")
    appointment_slots: Mapped[list["AppointmentSlot"]] = relationship(back_populates="business")


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (Index("ix_customers_business_phone", "business_id", "phone"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    business: Mapped[Business] = relationship(back_populates="customers")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="customer")


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (Index("ix_calls_business_started", "business_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    room_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="in_progress")
    # in_progress | completed | failed
    detected_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    business: Mapped[Business] = relationship(back_populates="calls")
    messages: Mapped[list["CallMessage"]] = relationship(
        back_populates="call", cascade="all, delete-orphan", order_by="CallMessage.timestamp"
    )
    extraction: Mapped["CallExtraction | None"] = relationship(
        back_populates="call", uselist=False, cascade="all, delete-orphan"
    )


class CallMessage(Base):
    __tablename__ = "call_messages"
    __table_args__ = (Index("ix_call_messages_call_id", "call_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | tool
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    call: Mapped[Call] = relationship(back_populates="messages")


class AppointmentSlot(Base):
    """Fake but deterministic bookable inventory, generated relative to "today"
    for the next N days. This is what `check_availability` actually queries so
    the agent never has to invent times."""

    __tablename__ = "appointment_slots"
    __table_args__ = (
        UniqueConstraint("business_id", "doctor", "date", "time", name="uq_slot"),
        Index("ix_slots_business_date", "business_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    doctor: Mapped[str] = mapped_column(String(100), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # ISO YYYY-MM-DD
    time: Mapped[str] = mapped_column(String(5), nullable=False)  # HH:MM (24h)
    is_booked: Mapped[bool] = mapped_column(Boolean, default=False)

    business: Mapped[Business] = relationship(back_populates="appointment_slots")


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appointments_business_date", "business_id", "date"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # ISO YYYY-MM-DD
    time: Mapped[str] = mapped_column(String(5), nullable=False)  # HH:MM
    service: Mapped[str] = mapped_column(String(100), nullable=False)
    doctor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="confirmed")  # confirmed | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    business: Mapped[Business] = relationship(back_populates="appointments")
    customer: Mapped[Customer] = relationship(back_populates="appointments")


class CallExtraction(Base):
    __tablename__ = "call_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id"), nullable=False, unique=True
    )
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    service: Mapped[str | None] = mapped_column(String(100), nullable=True)
    appointment_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    appointment_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requires_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    call: Mapped[Call] = relationship(back_populates="extraction")
