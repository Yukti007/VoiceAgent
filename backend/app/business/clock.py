"""The business's own clock.

"Today", "kal" and "is this slot in the past?" must be answered in the
clinic's timezone, not the server's: a worker on a UTC host would otherwise
treat 00:00-05:30 IST as the previous day. The timezone lives in the
business's knowledge JSON so each tenant can have its own.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.database.models import Business

DEFAULT_TIMEZONE = "Asia/Kolkata"


def business_timezone(business: Business | None) -> ZoneInfo:
    knowledge = (business.business_knowledge or {}) if business is not None else {}
    return ZoneInfo(knowledge.get("timezone") or DEFAULT_TIMEZONE)


def business_now(business: Business | None) -> datetime:
    """Current wall-clock time at the business (timezone-aware)."""
    return datetime.now(business_timezone(business))


def business_today(business: Business | None) -> date:
    return business_now(business).date()
