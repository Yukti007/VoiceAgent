"""System prompt assembly. Kept as a single small function so the prompt is
easy to tweak without touching agent wiring code."""

from __future__ import annotations

from datetime import date, timedelta

from app.database.models import Business

TOOL_USAGE_RULES = """
You have these tools available: check_availability, book_appointment,
book_group_appointment, get_business_hours, get_service_price. Rules for using them:
- Always call check_availability before telling the caller which specific times are open.
  Never invent or guess a time slot.
- Always call book_appointment before telling the caller a booking is confirmed. If it
  returns success=false, tell the caller honestly and offer to check other times.
- Tool arguments for dates must be exact ISO format YYYY-MM-DD, and times must be 24-hour
  HH:MM. Resolve relative expressions ("today", "kal"/"tomorrow", "Thursday", "agle hafte")
  to an exact date yourself before calling a tool.
- After a successful booking, clearly repeat the confirmed date, time, and service back to
  the caller. If the result has already_booked=true, the appointment was already made
  earlier in this call: confirm it to the caller, do not book again.
- If the caller wants appointments for more than one person (e.g. themselves and their
  child), collect every person's name and use book_group_appointment once, not
  book_appointment repeatedly.
- Only call a tool when the caller's request needs it. Do not look up prices or hours
  nobody asked about.
"""

LANGUAGE_RULE = """
LANGUAGE: Reply in the language of the caller's MOST RECENT message. If they spoke Hindi
(Devanagari), reply in Hindi; if English, reply in English; if Hinglish, reply in Hinglish.
Never answer a Hindi message in English. Do not wait to be asked to switch.
"""


def _calendar(today: date, days: int = 14) -> str:
    """Spell out the next two weeks so the model looks dates up instead of
    doing weekday arithmetic, which a live call showed it getting wrong
    ("Wednesday" resolved to a Monday)."""
    lines = [
        f"- {d.strftime('%A')} {d.day} {d.strftime('%B')} = {d.isoformat()}"
        for d in (today + timedelta(days=offset) for offset in range(days))
    ]
    return "Calendar for resolving day names to dates:\n" + "\n".join(lines)


def build_system_prompt(business: Business, knowledge_context: str) -> str:
    today = date.today()
    return f"""{business.system_instructions}
{LANGUAGE_RULE}
Today's date is {today.isoformat()} ({today.strftime("%A")}).
{_calendar(today)}
{TOOL_USAGE_RULES}

--- BUSINESS KNOWLEDGE (only use what is stated here; if asked about something not
covered, say a staff member may need to confirm it) ---
{knowledge_context}
--- END BUSINESS KNOWLEDGE ---
"""
