from datetime import date, timedelta

from app.database.database import session_scope
from app.database.models import AppointmentSlot, Business
from app.database.seed import AVAILABILITY_DAYS, DOCTORS


def test_sharma_dental_seeded(business_id):
    with session_scope() as session:
        business = session.get(Business, business_id)
        assert business is not None
        assert business.name == "Sharma Dental Care"
        assert business.agent_name == "Aisha"
        assert "en-IN" in business.supported_languages
        assert "hi-IN" in business.supported_languages
        assert business.greeting.startswith("Hello, thank you for calling")

        knowledge = business.business_knowledge
        service_names = {s["name"] for s in knowledge["services"]}
        assert service_names == {
            "Consultation",
            "Dental cleaning",
            "Teeth whitening",
            "Root canal",
            "Tooth extraction",
        }
        assert knowledge["doctors"] == DOCTORS
        assert "DEMO" in knowledge["insurance"]["_note"]


def test_availability_generated_relative_to_today(business_id):
    today = date.today()
    with session_scope() as session:
        count = (
            session.query(AppointmentSlot)
            .filter(AppointmentSlot.business_id == business_id)
            .count()
        )
        assert count > 0

        # No slots before today, none beyond the configured window.
        max_date = (today + timedelta(days=AVAILABILITY_DAYS - 1)).isoformat()
        min_date = today.isoformat()
        stale = (
            session.query(AppointmentSlot)
            .filter(
                AppointmentSlot.business_id == business_id,
                (AppointmentSlot.date < min_date) | (AppointmentSlot.date > max_date),
            )
            .count()
        )
        assert stale == 0


def test_sunday_has_no_slots(business_id):
    today = date.today()
    sunday = next(
        today + timedelta(days=i)
        for i in range(AVAILABILITY_DAYS)
        if (today + timedelta(days=i)).weekday() == 6
    )
    with session_scope() as session:
        count = (
            session.query(AppointmentSlot)
            .filter(
                AppointmentSlot.business_id == business_id,
                AppointmentSlot.date == sunday.isoformat(),
            )
            .count()
        )
        assert count == 0


def test_ensure_availability_tops_up_expired_window_without_touching_bookings(business_id):
    from datetime import date, timedelta

    from app.database.database import session_scope
    from app.database.models import AppointmentSlot
    from app.database.seed import AVAILABILITY_DAYS, ensure_availability

    # Simulate a seed that has aged: the last few days of the window have no slots.
    tail = [(date.today() + timedelta(days=d)).isoformat() for d in range(AVAILABILITY_DAYS - 3, AVAILABILITY_DAYS)]
    with session_scope() as session:
        session.query(AppointmentSlot).filter(
            AppointmentSlot.business_id == business_id, AppointmentSlot.date.in_(tail)
        ).delete(synchronize_session=False)
        booked_before = (
            session.query(AppointmentSlot)
            .filter(AppointmentSlot.business_id == business_id, AppointmentSlot.is_booked.is_(True))
            .count()
        )

    with session_scope() as session:
        assert ensure_availability(session, business_id) > 0

    with session_scope() as session:
        open_days = {
            d
            for (d,) in session.query(AppointmentSlot.date)
            .filter(AppointmentSlot.business_id == business_id, AppointmentSlot.date.in_(tail))
            .distinct()
        }
        assert open_days == {d for d in tail if date.fromisoformat(d).weekday() != 6}
        # A second run is a no-op.
        assert ensure_availability(session, business_id) == 0
        booked_after = (
            session.query(AppointmentSlot)
            .filter(
                AppointmentSlot.business_id == business_id,
                AppointmentSlot.is_booked.is_(True),
                AppointmentSlot.date.notin_(tail),
            )
            .count()
        )
    assert booked_after <= booked_before
