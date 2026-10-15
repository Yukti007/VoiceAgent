"""Customer lookup/creation helpers. Plain functions over a SQLAlchemy Session
so they're trivially unit-testable and reusable outside the voice agent."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import Customer


class InvalidPhoneError(ValueError):
    pass


def normalize_phone(raw: str | None) -> str:
    """Reduce a spoken/typed phone number to its 10 national digits.

    "98765 43210", "+91-98765-43210", "098765 43210" and Devanagari digits
    ("९८७६५४३२१०") all become "9876543210", so the same caller is always the
    same customer and a retried booking is recognised as a retry.
    """
    digits = "".join(str(int(c)) for c in (raw or "") if c.isdecimal())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10:
        raise InvalidPhoneError(
            f"'{raw}' is not a 10-digit phone number ({len(digits)} digits heard). "
            "Ask the caller to repeat it digit by digit."
        )
    return digits


def get_or_create_customer(
    session: Session,
    *,
    business_id: str,
    name: str,
    phone: str,
    email: str | None = None,
) -> Customer:
    """Find a customer by (business_id, phone, name), else create one.

    A phone number identifies a household, not a person: a parent books for
    a child on the same number. So a new name on a known phone is a new
    customer, never a rename of the existing one (which would silently move
    that person's past appointments to someone else)."""
    name = name.strip()
    existing = session.execute(
        select(Customer)
        .where(
            Customer.business_id == business_id,
            Customer.phone == phone,
            func.lower(Customer.name) == name.lower(),
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        if email and not existing.email:
            existing.email = email
        return existing

    customer = Customer(business_id=business_id, name=name, phone=phone, email=email)
    session.add(customer)
    session.flush()
    return customer
