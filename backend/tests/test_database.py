from sqlalchemy import inspect

from app.database.database import get_engine


def test_all_expected_tables_exist():
    inspector = inspect(get_engine())
    tables = set(inspector.get_table_names())
    expected = {
        "businesses",
        "customers",
        "calls",
        "call_messages",
        "appointment_slots",
        "appointments",
        "call_extractions",
    }
    assert expected.issubset(tables)
