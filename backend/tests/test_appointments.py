from datetime import date, timedelta

from app.database.database import session_scope
from app.database.models import Appointment, AppointmentSlot
from app.tools.appointments import book_appointment, check_availability, get_business_hours, get_service_price
from app.database.models import Business


def _first_weekday_with_availability(business_id: str) -> str:
    """Find a date within the seeded window that actually has an open slot,
    regardless of the random pre-booked pattern."""
    today = date.today()
    with session_scope() as session:
        for offset in range(14):
            d = (today + timedelta(days=offset)).isoformat()
            has_open = (
                session.query(AppointmentSlot)
                .filter(
                    AppointmentSlot.business_id == business_id,
                    AppointmentSlot.date == d,
                    AppointmentSlot.is_booked.is_(False),
                )
                .first()
            )
            if has_open:
                return d
    raise AssertionError("no available slot found in the seeded 14-day window")


def test_check_availability_returns_only_real_slots(business_id):
    target_date = _first_weekday_with_availability(business_id)
    with session_scope() as session:
        result = check_availability(session, business_id=business_id, date=target_date)

    assert result["date"] == target_date
    assert len(result["available"]) > 0
    for slot in result["available"]:
        assert slot["doctor"] in {"Dr. Raj Sharma", "Dr. Neha Mehta"}
        assert len(slot["time"]) == 5 and slot["time"][2] == ":"


def test_check_availability_invalid_date_returns_error(business_id):
    with session_scope() as session:
        result = check_availability(session, business_id=business_id, date="not-a-date")
    assert result["available"] == []
    assert "error" in result


def test_book_appointment_success_then_slot_unavailable(business_id):
    target_date = _first_weekday_with_availability(business_id)
    with session_scope() as session:
        available = check_availability(session, business_id=business_id, date=target_date)
        slot = available["available"][0]

        result = book_appointment(
            session,
            business_id=business_id,
            customer_name="Test Patient",
            customer_phone="9999900000",
            date=target_date,
            time=slot["time"],
            service="Consultation",
            doctor=slot["doctor"],
        )

    assert result["success"] is True
    assert result["date"] == target_date
    assert result["doctor"] == slot["doctor"]

    with session_scope() as session:
        appt = session.get(Appointment, result["appointment_id"])
        assert appt is not None
        assert appt.status == "confirmed"
        assert appt.customer.name == "Test Patient"

        # The exact slot should no longer appear as available.
        refreshed = check_availability(session, business_id=business_id, date=target_date, doctor=slot["doctor"])
        assert all(s["time"] != slot["time"] for s in refreshed["available"])


def test_book_appointment_rejects_already_booked_slot(business_id):
    target_date = _first_weekday_with_availability(business_id)
    with session_scope() as session:
        available = check_availability(session, business_id=business_id, date=target_date)
        slot = available["available"][0]

        first = book_appointment(
            session,
            business_id=business_id,
            customer_name="First Patient",
            customer_phone="9999900001",
            date=target_date,
            time=slot["time"],
            service="Dental cleaning",
            doctor=slot["doctor"],
        )
        assert first["success"] is True

        second = book_appointment(
            session,
            business_id=business_id,
            customer_name="Second Patient",
            customer_phone="9999900002",
            date=target_date,
            time=slot["time"],
            service="Dental cleaning",
            doctor=slot["doctor"],
        )

    assert second["success"] is False
    assert "error" in second


def test_get_business_hours_and_price(business_id):
    with session_scope() as session:
        business = session.get(Business, business_id)
        hours = get_business_hours(business, "sunday")
        assert hours["sunday"] == "Closed"

        price = get_service_price(business, "teeth whitening")
        assert price["price_inr"] == 4000

        missing = get_service_price(business, "brain surgery")
        assert missing is None


def test_concurrent_bookings_cannot_double_book_a_slot(business_id, monkeypatch):
    """Two callers (separate sessions/connections, like two worker processes)
    both see the same slot as free, then both try to book it. Exactly one may
    succeed."""
    import threading

    from app.tools import appointments as appointment_tools

    target_date = _first_weekday_with_availability(business_id)
    with session_scope() as session:
        slot = check_availability(session, business_id=business_id, date=target_date)["available"][0]

    both_have_read = threading.Barrier(2, timeout=5)
    original_find = appointment_tools._find_candidate_slots

    def find_then_wait(*args, **kwargs):
        candidates = original_find(*args, **kwargs)
        both_have_read.wait()  # force the classic read-read-write-write race
        return candidates

    monkeypatch.setattr(appointment_tools, "_find_candidate_slots", find_then_wait)

    results: list[dict] = []

    def attempt(phone: str) -> None:
        with session_scope() as session:
            results.append(
                book_appointment(
                    session,
                    business_id=business_id,
                    customer_name=f"Racer {phone}",
                    customer_phone=phone,
                    date=target_date,
                    time=slot["time"],
                    service="Consultation",
                    doctor=slot["doctor"],
                )
            )

    threads = [threading.Thread(target=attempt, args=(p,)) for p in ("9000000001", "9000000002")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(r["success"] for r in results) == [False, True]
    with session_scope() as session:
        booked = (
            session.query(Appointment)
            .filter(
                Appointment.business_id == business_id,
                Appointment.date == target_date,
                Appointment.time == slot["time"],
                Appointment.doctor == slot["doctor"],
            )
            .count()
        )
    assert booked == 1
