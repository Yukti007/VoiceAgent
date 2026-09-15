"""Business knowledge access.

`KnowledgeProvider` is the seam that lets V0's hardcoded/SQLite-backed business
knowledge be swapped later for a RAG-based `VectorKnowledgeProvider` without
touching the agent code that consumes it -- callers only ever ask for
`get_context()` / `get_business()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.database.models import Business


class BusinessNotFoundError(Exception):
    pass


class KnowledgeProvider(ABC):
    """Abstract source of business knowledge for the agent's system context.

    V0 implementation: `DatabaseKnowledgeProvider` (reads the seeded SQLite row).
    Future implementation: a `VectorKnowledgeProvider` doing RAG retrieval over
    uploaded documents/websites, selecting only the relevant knowledge chunks
    for a given user turn instead of dumping the whole business record.
    """

    @abstractmethod
    async def get_business(self, business_id: str) -> Business: ...

    @abstractmethod
    async def get_context(self, business_id: str) -> str:
        """Return a text block suitable for injection into the agent's system prompt."""
        ...


class DatabaseKnowledgeProvider(KnowledgeProvider):
    """V0: loads the full business record from SQLite and renders it as text."""

    def __init__(self, session: Session):
        self._session = session

    async def get_business(self, business_id: str) -> Business:
        business = self._session.get(Business, business_id)
        if business is None:
            raise BusinessNotFoundError(f"No business with id={business_id!r}")
        return business

    async def get_context(self, business_id: str) -> str:
        business = await self.get_business(business_id)
        return render_business_knowledge(business)


def render_business_knowledge(business: Business) -> str:
    """Render a Business row's structured knowledge into readable text for the
    system prompt. Kept separate from the provider so it's reusable/testable."""

    knowledge = business.business_knowledge or {}
    lines: list[str] = []

    lines.append(f"Business name: {business.name}")
    if business.description:
        lines.append(f"Description: {business.description}")

    hours = knowledge.get("hours") or {}
    if hours:
        lines.append("\nOpening hours:")
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
            if day in hours:
                lines.append(f"- {day.capitalize()}: {hours[day]}")

    services = knowledge.get("services") or []
    if services:
        lines.append("\nServices and pricing:")
        for svc in services:
            prefix = "starting " if svc.get("starting_at") else ""
            lines.append(f"- {svc['name']}: {prefix}₹{svc['price_inr']:,}")

    doctors = knowledge.get("doctors") or []
    if doctors:
        lines.append("\nDoctors: " + ", ".join(doctors))

    policies = knowledge.get("policies") or {}
    if policies:
        lines.append("\nPolicies:")
        for key, value in policies.items():
            lines.append(f"- {value}")

    insurance = knowledge.get("insurance") or {}
    insurers = insurance.get("accepted_insurers") or []
    if insurers:
        lines.append(
            "\nAccepted insurance providers (demo data): " + ", ".join(insurers)
        )

    if knowledge.get("address"):
        lines.append(f"\nAddress: {knowledge['address']}")
    if knowledge.get("phone_display"):
        lines.append(f"Clinic phone: {knowledge['phone_display']}")

    return "\n".join(lines)
