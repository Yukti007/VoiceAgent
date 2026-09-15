"""Customer lookup/creation helpers. Plain functions over a SQLAlchemy Session
so they're trivially unit-testable and reusable outside the voice agent."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Customer


def get_or_create_customer(
    session: Session,
    *,
    business_id: str,
    name: str,
    phone: str | None = None,
    email: str | None = None,
) -> Customer:
    """Find an existing customer by (business_id, phone) if a phone is given,
    otherwise create a new one. Fake/demo data -- no real PII validation."""

    if phone:
        existing = session.execute(
            select(Customer).where(
                Customer.business_id == business_id, Customer.phone == phone
            )
        ).scalar_one_or_none()
        if existing is not None:
            if name and existing.name != name:
                existing.name = name
            if email and not existing.email:
                existing.email = email
            return existing

    customer = Customer(business_id=business_id, name=name, phone=phone, email=email)
    session.add(customer)
    session.flush()
    return customer
