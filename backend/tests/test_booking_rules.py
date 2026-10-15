"""Regression tests for the booking rules the tools enforce in code, so the
LLM can't book something the clinic wouldn't accept."""

from collections import Counter
from datetime import datetime, timedelta

import pytest

from app.business.clock import business_timezone, business_today
from app.database.database import session_scope
from app.database.models import Appointment, AppointmentSlot, Business, Customer
from app.tools.appointments import (
    book_appointment,
    book_group_appointment,
    check_availability,
    get_service_price,
)
from app.tools.customer import InvalidPhoneError, normalize_phone


def _future_weekday(min_offset: int = 1) -> str:
    today = business_today(None)
    for offset in range(min_offset, 14):
        day = today + timedelta(days=offset)
        if day.weekday() < 5:
            return day.isoformat()
    raise AssertionError("no weekday in window")


def _open_time(business_id: str, date: str, *, both_doctors: bool = False) -> str:
    with session_scope() as session:
        open_slots = check_availability(session, business_id=business_id, date=date)["available"]
    counts = Counter(s["time"] for s in open_slots)
    times = [t for t, n in counts.items() if n == 2] if both_doctors else list(counts)
    assert times, "no suitable open slot"
    return times[0]


def _clinic_time(date: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{date}T{hhmm}").replace(tzinfo=business_timezone(None))


def _book(business_id: str, **overrides) -> dict:
    kwargs = dict(
        business_id=business_id,
        customer_name="Rule Patient",
        customer_phone="9000011111",
        service="Consultation",
    )
    kwargs.update(overrides)
    with session_scope() as session:
        return book_appointment(session, **kwargs)


# ---- dates and times ----------------------------------------------------


def test_past_dates_are_neither_offered_nor_bookable(business_id):
    past = (business_today(None) - timedelta(days=2)).isoformat()
    # ensure_availability never deletes old days, so stale open slots exist in practice.
    with session_scope() as session:
        stale = AppointmentSlot(business_id=business_id, doctor="Dr. Raj Sharma", date=past, time="10:00")
        session.add(stale)
        session.flush()
        stale_id = stale.id
    try:
        with session_scope() as session:
            result = check_availability(session, business_id=business_id, date=past)
        assert result["available"] == [] and "past" in result["error"]

        booked = _book(business_id, date=past, time="10:00")
        assert booked["success"] is False and "past" in booked["error"]
    finally:
        with session_scope() as session:
            session.query(AppointmentSlot).filter(AppointmentSlot.id == stale_id).delete()


def test_earlier_times_today_are_hidden_and_rejected(business_id):
    day = _future_weekday()
    now = _clinic_time(day, "13:30")

    with session_scope() as session:
        result = check_availability(session, business_id=business_id, date=day, now=now)
    assert result["available"], "afternoon slots should remain"
    assert all(s["time"] > "13:30" for s in result["available"])

    morning = _book(business_id, date=day, time="10:00", now=now)
    assert morning["success"] is False and "already passed" in morning["error"]


def test_dates_beyond_the_booking_window_are_rejected(business_id):
    far = (business_today(None) + timedelta(days=30)).isoformat()
    with session_scope() as session:
        result = check_availability(session, business_id=business_id, date=far)
    assert "only be booked up to" in result["error"]
    assert _book(business_id, date=far, time="10:00")["success"] is False


# ---- phone numbers ------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["9876543210", "98765 43210", "+91 98765-43210", "919876543210", "09876543210", "९८७६५४३२१०"],
)
def test_phone_numbers_normalise_to_ten_digits(raw):
    assert normalize_phone(raw) == "9876543210"


@pytest.mark.parametrize("raw", ["", "98765", "98765432101234"])
def test_bad_phone_numbers_are_rejected(raw):
    with pytest.raises(InvalidPhoneError):
        normalize_phone(raw)


def test_retry_with_differently_formatted_phone_is_not_a_second_booking(business_id):
    day = _future_weekday(2)
    time = _open_time(business_id, day, both_doctors=True)

    first = _book(business_id, customer_phone="98765 22222", date=day, time=time)
    retry = _book(business_id, customer_phone="+91 9876522222", date=day, time=time)

    assert first["success"] and retry["success"]
    assert retry.get("already_booked") is True
    assert retry["appointment_id"] == first["appointment_id"]
    with session_scope() as session:
        count = (
            session.query(Appointment)
            .join(Customer)
            .filter(Customer.phone == "9876522222", Appointment.date == day, Appointment.time == time)
            .count()
        )
    assert count == 1


def test_invalid_phone_books_nothing(business_id):
    day = _future_weekday()
    result = _book(business_id, customer_phone="12345", date=day, time=_open_time(business_id, day))
    assert result["success"] is False and "digit by digit" in result["error"]


# ---- services and doctors -----------------------------------------------


def test_unknown_service_is_rejected_with_the_catalogue(business_id):
    day = _future_weekday()
    result = _book(business_id, service="brain surgery", date=day, time=_open_time(business_id, day))
    assert result["success"] is False
    assert "Dental cleaning" in result["error"]


def test_spoken_service_names_resolve_to_the_catalogue(business_id):
    day = _future_weekday()
    result = _book(
        business_id, customer_phone="9000011112", service="cleaning", date=day, time=_open_time(business_id, day)
    )
    assert result["success"] is True and result["service"] == "Dental cleaning"

    with session_scope() as session:
        business = session.get(Business, business_id)
        assert get_service_price(business, "root canal treatment")["price_inr"] == 6000
        assert get_service_price(business, "teeth cleaning") is None  # whitening or cleaning: ask


def test_short_doctor_names_resolve_instead_of_looking_fully_booked(business_id):
    day = _future_weekday()
    with session_scope() as session:
        by_surname = check_availability(session, business_id=business_id, date=day, doctor="Dr. Sharma")
        honorific = check_availability(session, business_id=business_id, date=day, doctor="Neha ji")
        unknown = check_availability(session, business_id=business_id, date=day, doctor="Dr. Gupta")

    assert by_surname["available"] and {s["doctor"] for s in by_surname["available"]} == {"Dr. Raj Sharma"}
    assert honorific["available"] and {s["doctor"] for s in honorific["available"]} == {"Dr. Neha Mehta"}
    assert unknown["available"] == [] and "no doctor named" in unknown["error"]


# ---- customers on a shared phone ----------------------------------------


def test_single_booking_after_a_household_booking_does_not_crash_or_rename(business_id):
    day = _future_weekday(3)
    group_time = _open_time(business_id, day, both_doctors=True)
    with session_scope() as session:
        group = book_group_appointment(
            session,
            business_id=business_id,
            customer_phone="9111122222",
            patient_names=["Ravi Kapoor", "Anu Kapoor"],
            date=day,
            time=group_time,
            service="Dental cleaning",
        )
    assert group["success"] is True

    later = _future_weekday(4)
    single = _book(
        business_id,
        customer_name="Meera Kapoor",
        customer_phone="9111122222",
        date=later,
        time=_open_time(business_id, later),
    )
    assert single["success"] is True

    with session_scope() as session:
        names = {c.name for c in session.query(Customer).filter(Customer.phone == "9111122222")}
    assert names == {"Ravi Kapoor", "Anu Kapoor", "Meera Kapoor"}


# ---- tool error reporting -----------------------------------------------


async def test_db_errors_reach_the_llm_as_a_truthful_tool_error():
    from livekit.agents import ToolError

    from app.agent.agent import _run_db_tool

    def broken():
        raise RuntimeError("database is locked")

    with pytest.raises(ToolError) as excinfo:
        await _run_db_tool("book_appointment", broken)
    assert "nothing was saved" in str(excinfo.value)


# ---- preview (read-back) mode -------------------------------------------


def test_preview_validates_but_claims_nothing(business_id):
    day = _future_weekday(5)
    time = _open_time(business_id, day, both_doctors=True)
    preview = _book(business_id, customer_phone="+91 90000 22222", date=day, time=time, preview=True)
    assert preview["requires_confirmation"] is True
    assert preview["read_back"]["phone_digits"] == "9 0 0 0 0 2 2 2 2 2"
    assert time in _open_time_list(business_id, day)
    with session_scope() as session:
        assert session.query(Customer).filter(Customer.phone == "9000022222").count() == 0

    with session_scope() as session:
        group = book_group_appointment(
            session,
            business_id=business_id,
            customer_phone="9000022223",
            patient_names=["P One", "P Two"],
            date=day,
            time=time,
            service="Consultation",
            preview=True,
        )
    assert group["requires_confirmation"] is True and group["read_back"]["names"] == ["P One", "P Two"]
    with session_scope() as session:
        both = [s for s in check_availability(session, business_id=business_id, date=day)["available"] if s["time"] == time]
    assert len(both) == 2  # both doctors still free


def _open_time_list(business_id: str, date: str) -> list[str]:
    with session_scope() as session:
        return sorted({s["time"] for s in check_availability(session, business_id=business_id, date=date)["available"]})
