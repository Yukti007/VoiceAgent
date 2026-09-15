"""System prompt assembly. Kept as a single small function so the prompt is
easy to tweak without touching agent wiring code."""

from __future__ import annotations

from datetime import date

from app.database.models import Business

TOOL_USAGE_RULES = """
You have these tools available: check_availability, book_appointment, get_business_hours,
get_service_price. Rules for using them:
- Always call check_availability before telling the caller which specific times are open.
  Never invent or guess a time slot.
- Always call book_appointment before telling the caller a booking is confirmed. If it
  returns success=false, tell the caller honestly and offer to check other times.
- Tool arguments for dates must be exact ISO format YYYY-MM-DD, and times must be 24-hour
  HH:MM. Resolve relative expressions ("today", "kal"/"tomorrow", "Thursday", "agle hafte")
  to an exact date yourself before calling a tool.
- After a successful booking, clearly repeat the confirmed date, time, and service back to
  the caller.
"""


def build_system_prompt(business: Business, knowledge_context: str) -> str:
    today = date.today()
    return f"""{business.system_instructions}

Today's date is {today.isoformat()} ({today.strftime("%A")}).
{TOOL_USAGE_RULES}

--- BUSINESS KNOWLEDGE (only use what is stated here; if asked about something not
covered, say a staff member may need to confirm it) ---
{knowledge_context}
--- END BUSINESS KNOWLEDGE ---
"""
