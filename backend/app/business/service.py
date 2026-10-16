"""Business knowledge for the system prompt: the seeded business row rendered
as text. A retrieval-based source would replace `render_business_knowledge`
once a business has more knowledge than fits comfortably in the prompt."""

from __future__ import annotations

from app.database.models import Business


class BusinessNotFoundError(Exception):
    pass


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
