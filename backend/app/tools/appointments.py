"""Deterministic appointment tools.

These are plain, synchronous, fully-testable functions over a SQLAlchemy
Session. `app/agent/agent.py` wraps them as `@function_tool`s the LLM can call.
Keeping the logic here (instead of inline in the agent) means:
  - it can be unit tested without any LiveKit/LLM machinery
  - `check_availability` can never be "talked into" inventing a slot -- it only
    ever returns rows that actually exist in `appointment_slots`
  - `book_appointment` only ever reports success after a real SQLite write
  - two concurrent callers can never both get the same slot: claiming a slot
    is a single conditional UPDATE (see `_claim_slot`), not read-then-write
  - inputs the LLM passes are checked against the business's own data (past
    times, the booking window, the service catalogue, the doctor list, phone
    format) and rejected with an error message the LLM can act on
"""

from __future__ import annotations

import re
from datetime import date as date_cls
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.business.clock import business_now
from app.database.models import Appointment, AppointmentSlot, Business, Customer
from app.database.seed import AVAILABILITY_DAYS
from app.tools.customer import InvalidPhoneError, get_or_create_customer, normalize_phone

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


class BookingRuleError(ValueError):
    """A request the business can't accept (past time, unknown service, ...).
    The message is written for the LLM to relay or act on."""


def match_service(business: Business, service: str) -> dict | None:
    """Resolve a spoken service name to a catalogue entry, or None.

    Exact, then substring ("cleaning" -> "Dental cleaning"), then a word that
    names exactly one service. Ambiguous input ("teeth cleaning" fits both
    whitening and cleaning) returns None so the agent asks instead of guessing."""
    services = (business.business_knowledge or {}).get("services", [])
    needle = service.strip().lower()
    if not needle:
        return None
    for svc in services:
        if svc["name"].lower() == needle:
            return svc
    hits = [svc for svc in services if needle in svc["name"].lower() or svc["name"].lower() in needle]
    if len(hits) == 1:
        return hits[0]
    words = {w for w in re.findall(r"[a-z]+", needle) if len(w) >= 4}
    hits = [svc for svc in services if words & set(svc["name"].lower().split())]
    return hits[0] if len(hits) == 1 else None


_DOCTOR_FILLER_WORDS = {"dr", "doctor", "ji", "sir", "madam", "maam"}


def _name_words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", name.lower()) if w not in _DOCTOR_FILLER_WORDS}


def resolve_doctor(business: Business, doctor: str | None) -> str | None:
    """Map "Dr. Sharma" / "Neha ji" to the full name on file. None means
    "any doctor". Raises BookingRuleError for an unknown or ambiguous name --
    an exact-match filter used to turn "Dr. Sharma" into "fully booked"."""
    if doctor is None or not doctor.strip():
        return None
    doctors: list[str] = (business.business_knowledge or {}).get("doctors", [])
    wanted = _name_words(doctor)
    matches = [d for d in doctors if wanted and wanted <= _name_words(d)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise BookingRuleError(f"There is no doctor named '{doctor}'. Doctors: {', '.join(doctors)}.")
    raise BookingRuleError(f"'{doctor}' could mean {' or '.join(matches)}. Ask the caller which one.")


def _resolve_service(business: Business, service: str) -> str:
    match = match_service(business, service)
    if match is None:
        names = ", ".join(s["name"] for s in (business.business_knowledge or {}).get("services", []))
        raise BookingRuleError(f"'{service}' is not a service offered here. Services: {names}.")
    return match["name"]


def _check_date_bookable(day: date_cls, now: datetime) -> None:
    today = now.date()
    if day < today:
        raise BookingRuleError(
            f"{day.isoformat()} is in the past (today is {today.isoformat()}). Ask for a future date."
        )
    last = today + timedelta(days=AVAILABILITY_DAYS - 1)
    if day > last:
        raise BookingRuleError(
            f"Appointments can only be booked up to {last.isoformat()} ({AVAILABILITY_DAYS} days ahead)."
        )


def _check_slot_bookable(day: date_cls, time: str, now: datetime) -> None:
    _check_date_bookable(day, now)
    if day == now.date() and time <= now.strftime("%H:%M"):
        raise BookingRuleError(
            f"{time} today has already passed (it is {now.strftime('%H:%M')} at the clinic). "
            "Offer a later time."
        )


def _load_business(session: Session, business_id: str) -> Business:
    business = session.get(Business, business_id)
    if business is None:
        raise BookingRuleError(f"Unknown business '{business_id}'.")
    return business


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
    """Price for a service, matched the same way bookings match services."""
    svc = match_service(business, service)
    if svc is None:
        return None
    return {
        "service": svc["name"],
        "price_inr": svc["price_inr"],
        "starting_at": svc.get("starting_at", False),
    }


def check_availability(
    session: Session,
    *,
    business_id: str,
    date: str,
    doctor: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Query real generated availability for a given ISO date.

    Only future slots are returned: on today's date, times that have already
    passed at the clinic are left out.

    Returns:
        {"date": "2026-09-16", "available": [{"doctor": "...", "time": "09:00"}, ...]}
        or {"date": "...", "available": [], "error": "..."} for an invalid/out-of-range date.
    """
    try:
        parsed = _parse_iso_date(date)
        business = _load_business(session, business_id)
        now = now or business_now(business)
        _check_date_bookable(parsed, now)
        doctor = resolve_doctor(business, doctor)
    except (InvalidDateError, BookingRuleError) as exc:
        return {"date": date, "available": [], "error": str(exc)}

    query = select(AppointmentSlot).where(
        AppointmentSlot.business_id == business_id,
        AppointmentSlot.date == parsed.isoformat(),
        AppointmentSlot.is_booked.is_(False),
    )
    if doctor:
        query = query.where(AppointmentSlot.doctor == doctor)
    if parsed == now.date():
        query = query.where(AppointmentSlot.time > now.strftime("%H:%M"))
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
    now: datetime | None = None,
) -> dict:
    """Actually reserve a slot and create an Appointment row. Never invents
    availability -- fails with success=False if the slot doesn't exist or is booked."""
    try:
        parsed_date = _parse_iso_date(date)
        parsed_time = _parse_time(time)
        customer_phone = normalize_phone(customer_phone)
        if not customer_name.strip():
            raise BookingRuleError("The caller's name is required.")
        business = _load_business(session, business_id)
        _check_slot_bookable(parsed_date, parsed_time, now or business_now(business))
        service = _resolve_service(business, service)
        doctor = resolve_doctor(business, doctor)
    except (InvalidDateError, InvalidPhoneError, BookingRuleError) as exc:
        return {"success": False, "error": str(exc)}

    # Idempotency: if this caller already holds a confirmed appointment at
    # exactly this date/time, this is a retry (e.g. the confirmation was
    # interrupted and the LLM called the tool again), not a second booking.
    existing = _find_existing_booking(
        session,
        business_id=business_id,
        customer_phone=customer_phone,
        date=parsed_date.isoformat(),
        time=parsed_time,
    )
    if existing is not None:
        return _booking_result(existing, existing.customer.name, already_booked=True)

    slot = None
    for candidate in _find_candidate_slots(
        session,
        business_id=business_id,
        date=parsed_date.isoformat(),
        time=parsed_time,
        doctor=doctor,
    ):
        if _claim_slot(session, candidate.id):
            slot = candidate
            break

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

    return _booking_result(appointment, customer.name)


MAX_GROUP_SIZE = 4


def book_group_appointment(
    session: Session,
    *,
    business_id: str,
    customer_phone: str,
    patient_names: list[str],
    date: str,
    time: str,
    service: str,
    now: datetime | None = None,
) -> dict:
    """Book several people (e.g. a parent and child) at the same date/time,
    one slot each, all-or-nothing: if there aren't enough open slots for
    everyone, nothing is booked and the caller can pick another time."""
    try:
        parsed_date = _parse_iso_date(date)
        parsed_time = _parse_time(time)
        customer_phone = normalize_phone(customer_phone)
        business = _load_business(session, business_id)
        _check_slot_bookable(parsed_date, parsed_time, now or business_now(business))
        service = _resolve_service(business, service)
    except (InvalidDateError, InvalidPhoneError, BookingRuleError) as exc:
        return {"success": False, "error": str(exc)}

    names: list[str] = []
    for raw in patient_names:
        name = raw.strip()
        if name and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    if not names:
        return {"success": False, "error": "No patient names given."}
    if len(names) > MAX_GROUP_SIZE:
        return {"success": False, "error": f"At most {MAX_GROUP_SIZE} people can be booked together."}

    iso_date = parsed_date.isoformat()
    booked: list[dict] = []
    pending: list[str] = []
    for name in names:
        existing = _find_existing_booking(
            session,
            business_id=business_id,
            customer_phone=customer_phone,
            date=iso_date,
            time=parsed_time,
            customer_name=name,
        )
        if existing is not None:
            booked.append(_booking_result(existing, existing.customer.name, already_booked=True))
        else:
            pending.append(name)

    claimed: list[AppointmentSlot] = []
    for candidate in _find_candidate_slots(
        session, business_id=business_id, date=iso_date, time=parsed_time, doctor=None
    ):
        if len(claimed) == len(pending):
            break
        if _claim_slot(session, candidate.id):
            claimed.append(candidate)

    if len(claimed) < len(pending):
        if claimed:
            session.execute(
                update(AppointmentSlot)
                .where(AppointmentSlot.id.in_([slot.id for slot in claimed]))
                .values(is_booked=False)
                .execution_options(synchronize_session=False)
            )
        return {
            "success": False,
            "error": (
                f"Only {len(claimed)} open slot(s) on {iso_date} at {parsed_time}, "
                f"but {len(pending)} needed. Nothing was booked. Offer another time, "
                "or book them at back-to-back times with book_appointment."
            ),
        }

    for name, slot in zip(pending, claimed):
        customer = get_or_create_customer(
            session, business_id=business_id, name=name, phone=customer_phone
        )
        appointment = Appointment(
            business_id=business_id,
            customer_id=customer.id,
            date=iso_date,
            time=parsed_time,
            service=service,
            doctor=slot.doctor,
            status="confirmed",
        )
        session.add(appointment)
        session.flush()
        booked.append(_booking_result(appointment, customer.name))

    return {"success": True, "date": iso_date, "time": parsed_time, "appointments": booked}


def _booking_result(appointment: Appointment, customer_name: str, *, already_booked: bool = False) -> dict:
    result = {
        "success": True,
        "appointment_id": appointment.id,
        "date": appointment.date,
        "time": appointment.time,
        "service": appointment.service,
        "doctor": appointment.doctor,
        "customer_name": customer_name,
    }
    if already_booked:
        result["already_booked"] = True
    return result


def _find_existing_booking(
    session: Session,
    *,
    business_id: str,
    customer_phone: str | None,
    date: str,
    time: str,
    customer_name: str | None = None,
) -> Appointment | None:
    if not customer_phone:
        return None
    query = (
        select(Appointment)
        .join(Customer, Appointment.customer_id == Customer.id)
        .where(
            Appointment.business_id == business_id,
            Appointment.date == date,
            Appointment.time == time,
            Appointment.status == "confirmed",
            Customer.phone == customer_phone,
        )
    )
    if customer_name:
        # Household bookings share a phone, so the name tells retries apart
        # from a second family member at the same time.
        query = query.where(func.lower(Customer.name) == customer_name.strip().lower())
    return session.execute(query.limit(1)).scalar_one_or_none()


def _find_candidate_slots(
    session: Session, *, business_id: str, date: str, time: str, doctor: str | None
) -> list[AppointmentSlot]:
    """Open slots matching the request. This read is only a hint -- another
    caller may take any of them before we claim it, which `_claim_slot` handles."""
    query = select(AppointmentSlot).where(
        AppointmentSlot.business_id == business_id,
        AppointmentSlot.date == date,
        AppointmentSlot.time == time,
        AppointmentSlot.is_booked.is_(False),
    )
    if doctor:
        query = query.where(AppointmentSlot.doctor == doctor)
    return list(session.execute(query.order_by(AppointmentSlot.doctor)).scalars().all())


def _claim_slot(session: Session, slot_id: int) -> bool:
    """Atomically mark a slot booked, but only if it's still free.

    A single `UPDATE ... WHERE is_booked = false` is atomic in both SQLite
    and Postgres: if a concurrent booking (another call, in another worker
    process) got there first, this matches zero rows and we report the slot
    as taken instead of silently double-booking it.
    """
    result = session.execute(
        update(AppointmentSlot)
        .where(AppointmentSlot.id == slot_id, AppointmentSlot.is_booked.is_(False))
        .values(is_booked=True)
        .execution_options(synchronize_session=False)
    )
    claimed = result.rowcount == 1
    if claimed:
        slot = session.get(AppointmentSlot, slot_id)
        if slot is not None:
            session.refresh(slot)
    return claimed
