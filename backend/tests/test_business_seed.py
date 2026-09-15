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
