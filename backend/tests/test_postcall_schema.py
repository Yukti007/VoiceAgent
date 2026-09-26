import pytest
from pydantic import ValidationError

from app.postcall.schemas import CallExtraction


def test_valid_extraction_parses():
    extraction = CallExtraction.model_validate(
        {
            "intent": "appointment_booking",
            "customer_name": "Rahul Sharma",
            "customer_phone": "98765 43210",
            "language": "Hinglish",
            "service": "dental consultation",
            "appointment_date": "2026-09-20",
            "appointment_time": "14:00",
            "outcome": "appointment_booked",
            "requires_followup": False,
            "summary": "Customer booked a dental consultation.",
        }
    )
    assert extraction.customer_phone == "9876543210"  # non-digits stripped
    assert extraction.appointment_date == "2026-09-20"


def test_invalid_date_format_rejected():
    with pytest.raises(ValidationError):
        CallExtraction.model_validate(
            {
                "intent": "appointment_booking",
                "language": "English",
                "outcome": "appointment_booked",
                "summary": "x",
                "appointment_date": "20-09-2026",
            }
        )


def test_invalid_time_format_rejected():
    with pytest.raises(ValidationError):
        CallExtraction.model_validate(
            {
                "intent": "appointment_booking",
                "language": "English",
                "outcome": "appointment_booked",
                "summary": "x",
                "appointment_time": "2 PM",
            }
        )


def test_missing_optional_fields_default_sensibly():
    extraction = CallExtraction.model_validate(
        {
            "intent": "general_inquiry",
            "language": "English",
            "outcome": "information_provided",
            "summary": "Customer asked about opening hours.",
        }
    )
    assert extraction.customer_name is None
    assert extraction.requires_followup is False


def test_null_required_fields_fall_back_instead_of_failing():
    # A short call once produced language=null, which dropped the whole extraction.
    extraction = CallExtraction.model_validate(
        {"intent": None, "language": None, "outcome": "", "summary": None}
    )
    assert extraction.intent == "unknown"
    assert extraction.language == "unknown"
    assert extraction.outcome == "unknown"
    assert extraction.summary == ""
