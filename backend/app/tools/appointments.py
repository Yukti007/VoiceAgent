"""Deterministic appointment tools.

These are plain, synchronous, fully-testable functions over a SQLAlchemy
Session. `app/agent/agent.py` wraps them as `@function_tool`s the LLM can call.
Keeping the logic here (instead of inline in the agent) means:
  - it can be unit tested without any LiveKit/LLM machinery
  - `check_availability` can never be "talked into" inventing a slot -- it only
    ever returns rows that actually exist in `appointment_slots`
  - `book_appointment` only ever reports success after a real SQLite write
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Appointment, AppointmentSlot, Business
from app.tools.customer import get_or_create_customer

WEEKDAY_NAMES = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


class InvalidDateError(ValueError):
    pass


def _parse_iso_date(date_str: str) -> date_cls:
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise InvalidDateError(
            f"'{date_str}' is not a valid ISO date (expected YYYY-MM-DD)"
        ) from exc


def _parse_time(time_str: str) -> str:
    """Normalize a time string to 24h HH:MM. Accepts '14:00', '2:00 PM', '2 PM'."""
    time_str = time_str.strip().upper().replace(".", "")
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
        try:
            return datetime.strptime(time_str, fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise InvalidDateError(f"'{time_str}' is not a recognizable time (expected e.g. '14:00' or '2:00 PM')")


def get_business_hours(business: Business, day: str | None = None) -> dict:
    """Return opening hours. If `day` is given (e.g. 'monday' or 'Thursday'),
    returns just that day's hours; otherwise the full week."""
    hours = (business.business_knowledge or {}).get("hours", {})
    if day is None:
        return dict(hours)
    key = day.strip().lower()
    if key not in hours:
        return {"error": f"Unknown day '{day}'"}
    return {key: hours[key]}


def get_service_price(business: Business, service: str) -> dict | None:
    """Fuzzy (case-insensitive substring) match against the seeded service list."""
    services = (business.business_knowledge or {}).get("services", [])
    needle = service.strip().lower()
    for svc in services:
        if needle in svc["name"].lower() or svc["name"].lower() in needle:
            return {
                "service": svc["name"],
                "price_inr": svc["price_inr"],
                "starting_at": svc.get("starting_at", False),
            }
    return None


def check_availability(
    session: Session, *, business_id: str, date: str, doctor: str | None = None
) -> dict:
    """Query real generated availability for a given ISO date.

    Returns:
        {"date": "2026-09-16", "available": [{"doctor": "...", "time": "09:00"}, ...]}
        or {"date": "...", "available": [], "error": "..."} for an invalid/out-of-range date.
    """
    try:
        parsed = _parse_iso_date(date)
    except InvalidDateError as exc:
        return {"date": date, "available": [], "error": str(exc)}

    query = select(AppointmentSlot).where(
        AppointmentSlot.business_id == business_id,
        AppointmentSlot.date == parsed.isoformat(),
        AppointmentSlot.is_booked.is_(False),
    )
    if doctor:
        query = query.where(AppointmentSlot.doctor == doctor)
    query = query.order_by(AppointmentSlot.time, AppointmentSlot.doctor)

    slots = session.execute(query).scalars().all()
    if not slots:
        # Distinguish "closed that day / out of generated range" from "just fully booked"
        any_slot_exists = session.execute(
            select(AppointmentSlot.id)
            .where(AppointmentSlot.business_id == business_id, AppointmentSlot.date == parsed.isoformat())
            .limit(1)
        ).first()
        if any_slot_exists is None:
            return {
                "date": parsed.isoformat(),
                "available": [],
                "error": "This date is either outside the available booking window or the clinic is closed that day.",
            }

    return {
        "date": parsed.isoformat(),
        "weekday": WEEKDAY_NAMES[parsed.weekday()],
        "available": [{"doctor": s.doctor, "time": s.time} for s in slots],
    }


def book_appointment(
    session: Session,
    *,
    business_id: str,
    customer_name: str,
    customer_phone: str,
    date: str,
    time: str,
    service: str,
    doctor: str | None = None,
) -> dict:
    """Actually reserve a slot and create an Appointment row. Never invents
    availability -- fails with success=False if the slot doesn't exist or is booked."""
    try:
        parsed_date = _parse_iso_date(date)
        parsed_time = _parse_time(time)
    except InvalidDateError as exc:
        return {"success": False, "error": str(exc)}

    query = select(AppointmentSlot).where(
        AppointmentSlot.business_id == business_id,
        AppointmentSlot.date == parsed_date.isoformat(),
        AppointmentSlot.time == parsed_time,
        AppointmentSlot.is_booked.is_(False),
    )
    if doctor:
        query = query.where(AppointmentSlot.doctor == doctor)
    slot = session.execute(query.limit(1)).scalar_one_or_none()

    if slot is None:
        return {
            "success": False,
            "error": (
                f"No available slot on {parsed_date.isoformat()} at {parsed_time}"
                + (f" with {doctor}" if doctor else "")
                + ". Call check_availability again for accurate options."
            ),
        }

    customer = get_or_create_customer(
        session, business_id=business_id, name=customer_name, phone=customer_phone
    )

    slot.is_booked = True

    appointment = Appointment(
        business_id=business_id,
        customer_id=customer.id,
        date=parsed_date.isoformat(),
        time=parsed_time,
        service=service,
        doctor=slot.doctor,
        status="confirmed",
    )
    session.add(appointment)
    session.flush()

    return {
        "success": True,
        "appointment_id": appointment.id,
        "date": appointment.date,
        "time": appointment.time,
        "service": appointment.service,
        "doctor": appointment.doctor,
        "customer_name": customer.name,
    }
