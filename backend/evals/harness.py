"""A scripted caller for the real agent, in LiveKit's text mode (no audio):
the same Assistant, system prompt, tools and LLM as a live call, so what is
tested is the LLM's tool use and the database outcome. STT, TTS, turn-taking
and barge-in are audio-level and not exercised here."""

from __future__ import annotations

import ast
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from livekit.agents import AgentSession, APIConnectOptions
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.agents.voice.run_result import RunResult

from app.agent.agent import Assistant
from app.agent.prompts import build_system_prompt
from app.agent.session import create_call
from app.agent.state import SessionData
from app.business.clock import business_now
from app.business.service import render_business_knowledge
from app.config import get_settings
from app.database.database import session_scope
from app.database.models import Business
from app.llm.provider import get_agent_llm_with_fallback

# Free-tier LLM keys rate-limit by tokens per minute; wait those windows out
# instead of failing the scenario (a live call uses fail-fast settings).
EVAL_CONN_OPTIONS = SessionConnectOptions(
    llm_conn_options=APIConnectOptions(max_retry=8, retry_interval=8.0, timeout=60.0),
)


class Conversation:
    def __init__(self, session: AgentSession, call_id: str) -> None:
        self.session = session
        self.call_id = call_id
        self.events: list = []
        self.lines: list[str] = []
        self.last_result: RunResult | None = None

    async def say(self, text: str) -> str:
        """One caller turn. Returns the agent's reply text for that turn."""
        self.lines.append(f"CALLER: {text}")
        result = await self.session.run(user_input=text)
        self.last_result = result
        reply = []
        for ev in result.events:
            self.events.append(ev)
            if ev.type == "function_call":
                self.lines.append(f"  -> {ev.item.name}({ev.item.arguments})")
            elif ev.type == "function_call_output":
                self.lines.append(f"  <- {ev.item.output[:300]}")
            elif ev.type == "message" and ev.item.role == "assistant":
                reply.append(ev.item.text_content or "")
                self.lines.append(f"AGENT: {ev.item.text_content}")
        return " ".join(reply)

    async def confirm_until(
        self, done: Callable[[], bool], text: str = "Yes, that's all correct. Please go ahead.", attempts: int = 3
    ) -> None:
        """Answer the agent's confirmation questions until `done()` holds."""
        for _ in range(attempts):
            if done():
                return
            await self.say(text)

    def calls(self, name: str | None = None) -> list[dict]:
        """Arguments of every tool call so far, in order (optionally one tool's)."""
        return [
            {"_name": ev.item.name, **json.loads(ev.item.arguments or "{}")}
            for ev in self.events
            if ev.type == "function_call" and (name is None or ev.item.name == name)
        ]

    def outputs(self, name: str) -> list[dict | str]:
        """Parsed outputs of one tool's calls, in order."""
        call_ids = {ev.item.call_id for ev in self.events if ev.type == "function_call" and ev.item.name == name}
        parsed: list[dict | str] = []
        for ev in self.events:
            if ev.type == "function_call_output" and ev.item.call_id in call_ids:
                try:
                    parsed.append(ast.literal_eval(ev.item.output))
                except (ValueError, SyntaxError):
                    parsed.append(ev.item.output)
        return parsed

    def tool_order(self) -> list[str]:
        return [c["_name"] for c in self.calls()]

    def save_transcript(self, path: Path, seconds: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + f"\n\n[{seconds:.0f}s]\n", encoding="utf-8")


@asynccontextmanager
async def open_conversation(business_id: str) -> AsyncIterator[Conversation]:
    settings = get_settings()
    with session_scope() as db:
        business = db.get(Business, business_id)
        instructions = build_system_prompt(business, render_business_knowledge(business))
    call_id = create_call(business_id, room_name=f"eval-{uuid.uuid4().hex[:8]}")
    session: AgentSession[SessionData] = AgentSession[SessionData](
        llm=get_agent_llm_with_fallback(settings),
        userdata=SessionData(business_id=business_id, call_id=call_id),
        conn_options=EVAL_CONN_OPTIONS,
    )
    async with session:
        await session.start(Assistant(instructions=instructions))
        yield Conversation(session, call_id)


# ---- Wording helpers for scripted callers -------------------------------

HINDI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]


def clinic_today() -> date:
    with session_scope() as db:
        return business_now(db.get(Business, get_settings().default_business_id)).date()


def weekdays_ahead(min_offset: int = 2) -> list[date]:
    """Mon-Fri dates inside the booking window, at least `min_offset` days out."""
    today = clinic_today()
    return [today + timedelta(days=i) for i in range(min_offset, 14) if (today + timedelta(days=i)).weekday() < 5]


def say_date(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def say_date_hindi(d: date) -> str:
    return f"{d.day} {HINDI_MONTHS[d.month - 1]}"


def say_time(hhmm: str) -> str:
    hour = int(hhmm[:2])
    return f"{hour % 12 or 12} {'am' if hour < 12 else 'pm'}"


def say_time_hindi(hhmm: str) -> str:
    hour = int(hhmm[:2])
    part = "सुबह" if hour < 12 else "दोपहर" if hour < 16 else "शाम"
    return f"{part} {hour % 12 or 12}"
