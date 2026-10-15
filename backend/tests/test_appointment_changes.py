"""Cancelling and rescheduling: slots are released, ownership is checked, the
24-hour policy is enforced in code, and a failed reschedule changes nothing."""

from collections import Counter
from datetime import datetime, timedelta

from app.business.clock import business_timezone, business_today
from app.database.database import session_scope
from app.database.models import Appointment, AppointmentSlot
from app.tools.appointments import (
    book_appointment,
    cancel_appointment,
    check_availability,
    find_appointments,
    reschedule_appointment,
)


def _weekday(min_offset: int) -> str:
    today = business_today(None)
    return next(
        (today + timedelta(days=i)).isoformat()
        for i in range(min_offset, 14)
        if (today + timedelta(days=i)).weekday() < 5
    )


def _open_times(business_id: str, date: str) -> list[str]:
    with session_scope() as session:
        slots = check_availability(session, business_id=business_id, date=date)["available"]
    return sorted(Counter(s["time"] for s in slots))


def _slot_is_booked(business_id: str, doctor: str, date: str, time: str) -> bool:
    with session_scope() as session:
        slot = (
            session.query(AppointmentSlot)
            .filter_by(business_id=business_id, doctor=doctor, date=date, time=time)
            .one()
        )
        return slot.is_booked


def _book(business_id: str, phone: str, date: str, time: str) -> dict:
    with session_scope() as session:
        result = book_appointment(
            session,
            business_id=business_id,
            customer_name="Change Patient",
            customer_phone=phone,
            date=date,
            time=time,
            service="Consultation",
        )
    assert result["success"], result
    return result


def _days_before(appointment: dict, hours: float) -> datetime:
    starts = datetime.fromisoformat(f"{appointment['date']}T{appointment['time']}")
    return starts.replace(tzinfo=business_timezone(None)) - timedelta(hours=hours)


def test_find_lists_upcoming_appointments_for_the_phone(business_id):
    day = _weekday(2)
    booked = _book(business_id, "9222200001", day, _open_times(business_id, day)[0])
    with session_scope() as session:
        found = find_appointments(session, business_id=business_id, customer_phone="+91 92222 00001")
        nobody = find_appointments(session, business_id=business_id, customer_phone="9222200099")
    assert [a["appointment_id"] for a in found["appointments"]] == [booked["appointment_id"]]
    assert nobody["appointments"] == []


def test_cancel_releases_the_slot_and_is_idempotent(business_id):
    day = _weekday(3)
    booked = _book(business_id, "9222200002", day, _open_times(business_id, day)[0])
    assert _slot_is_booked(business_id, booked["doctor"], day, booked["time"])

    kwargs = dict(business_id=business_id, appointment_id=booked["appointment_id"], customer_phone="9222200002")
    with session_scope() as session:
        result = cancel_appointment(session, **kwargs)
    assert result["success"] and result["status"] == "cancelled"
    assert not _slot_is_booked(business_id, booked["doctor"], day, booked["time"])

    with session_scope() as session:
        again = cancel_appointment(session, **kwargs)
    assert again == {"success": True, "appointment_id": booked["appointment_id"], "already_cancelled": True}


def test_cannot_change_someone_elses_appointment(business_id):
    day = _weekday(3)
    booked = _book(business_id, "9222200003", day, _open_times(business_id, day)[0])
    with session_scope() as session:
        result = cancel_appointment(
            session, business_id=business_id, appointment_id=booked["appointment_id"], customer_phone="9222200004"
        )
    assert result["success"] is False
    assert _slot_is_booked(business_id, booked["doctor"], day, booked["time"])


def test_late_cancellation_needs_the_caller_to_accept_the_policy(business_id):
    day = _weekday(2)
    booked = _book(business_id, "9222200005", day, _open_times(business_id, day)[0])
    now = _days_before(booked, hours=5)
    kwargs = dict(
        business_id=business_id, appointment_id=booked["appointment_id"], customer_phone="9222200005", now=now
    )

    with session_scope() as session:
        first = cancel_appointment(session, **kwargs)
    assert first["success"] is False and first["requires_confirmation"] is True
    assert "24 hours" in first["error"]
    assert _slot_is_booked(business_id, booked["doctor"], day, booked["time"])

    with session_scope() as session:
        accepted = cancel_appointment(session, accept_late_policy=True, **kwargs)
    assert accepted["success"] and accepted["late_change"] is True


def test_reschedule_moves_the_booking_and_frees_the_old_slot(business_id):
    day = _weekday(4)
    old_time, new_time = _open_times(business_id, day)[:2]
    booked = _book(business_id, "9222200006", day, old_time)

    with session_scope() as session:
        moved = reschedule_appointment(
            session,
            business_id=business_id,
            appointment_id=booked["appointment_id"],
            customer_phone="9222200006",
            new_date=day,
            new_time=new_time,
        )
    assert moved["success"] and moved["time"] == new_time and moved["previous_time"] == old_time
    assert moved["appointment_id"] == booked["appointment_id"]
    assert not _slot_is_booked(business_id, booked["doctor"], day, old_time)
    assert _slot_is_booked(business_id, moved["doctor"], day, new_time)
    with session_scope() as session:
        assert session.query(Appointment).join(Appointment.customer).filter_by(phone="9222200006").count() == 1


def test_failed_reschedule_leaves_the_original_untouched(business_id):
    day = _weekday(5)
    booked = _book(business_id, "9222200007", day, _open_times(business_id, day)[0])
    with session_scope() as session:
        result = reschedule_appointment(
            session,
            business_id=business_id,
            appointment_id=booked["appointment_id"],
            customer_phone="9222200007",
            new_date=day,
            new_time="23:00",  # the clinic has no slot then
        )
    assert result["success"] is False and "unchanged" in result["error"]
    with session_scope() as session:
        appointment = session.get(Appointment, booked["appointment_id"])
        assert (appointment.time, appointment.status) == (booked["time"], "confirmed")
    assert _slot_is_booked(business_id, booked["doctor"], day, booked["time"])


def test_reschedule_into_the_past_is_rejected(business_id):
    day = _weekday(5)
    booked = _book(business_id, "9222200008", day, _open_times(business_id, day)[0])
    yesterday = (business_today(None) - timedelta(days=1)).isoformat()
    with session_scope() as session:
        result = reschedule_appointment(
            session,
            business_id=business_id,
            appointment_id=booked["appointment_id"],
            customer_phone="9222200008",
            new_date=yesterday,
            new_time="10:00",
        )
    assert result["success"] is False and "past" in result["error"]
