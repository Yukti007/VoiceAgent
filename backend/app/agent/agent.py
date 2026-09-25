"""LiveKit Agents worker entrypoint for Aisha, the Sharma Dental Care receptionist.

Pipeline: browser mic -> LiveKit -> Sarvam Saaras realtime STT -> this Agent's
LLM (provider-abstracted, OpenAI by default) -> Sarvam Bulbul streaming TTS ->
LiveKit -> browser speaker. Turn detection/interruption/barge-in is handled by
AgentSession itself (VAD-based here) -- this file does not reinvent any of that.

Run with:
    python -m app.agent.agent dev
"""

from __future__ import annotations

import asyncio
import logging
import sys

from dotenv import load_dotenv

from app.config import PROJECT_ROOT, ensure_dirs, get_settings
from app.database.database import init_db, session_scope
from app.database.models import Business

# Windows' console defaults to the system codepage (e.g. cp1252), which can't
# encode Devanagari/Hindi transcript text and other non-ASCII output -- this
# demo is specifically about Hindi/Hinglish, so force UTF-8 stdout/stderr
# instead of silently corrupting/dropping log lines with Unicode in them.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from livekit.agents import (  # noqa: E402
    Agent,
    AgentSession,
    APIConnectOptions,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    room_io,
)
from livekit.agents.voice.agent_session import SessionConnectOptions  # noqa: E402
from livekit.plugins import sarvam, silero  # noqa: E402

# All three of these are set to the SAME rate (16kHz) on purpose: it's the
# rate Sarvam's realtime STT and Silero VAD both require natively. If the
# room negotiates a different input rate, livekit-rtc's native resampler
# (libsoxr) has to run on every audio frame -- and on Windows this project
# has hit a native crash in that resampler (a Visual C++ assertion inside
# livekit_ffi.dll / soxr's FFT cache, "LSX_FFT_BR == NULL", most likely a
# known libsoxr thread-safety bug when multiple resamplers are created
# concurrently). Matching rates end-to-end means the resampler is never
# invoked on the input leg at all, which avoids the crash entirely rather
# than working around it after the fact.
AUDIO_SAMPLE_RATE_IN = 16000
# Output side: Bulbul TTS can synthesize directly at 24kHz, which is also
# AgentSession's own default room-output rate -- so, same idea, no resample
# needed on the way out either.
AUDIO_SAMPLE_RATE_OUT = 24000

from app.agent import session as call_session  # noqa: E402
from app.agent.prompts import build_system_prompt  # noqa: E402
from app.agent.state import SessionData  # noqa: E402
from app.business.service import BusinessNotFoundError, render_business_knowledge  # noqa: E402
from app.llm.provider import get_agent_llm_with_fallback  # noqa: E402
from app.postcall.extractor import run_post_call_extraction  # noqa: E402
from app.tools import appointments as appointment_tools  # noqa: E402

logger = logging.getLogger("voice_agent.worker")

# Spoken when a turn is lost to a provider failure, so the caller isn't left
# in silence wondering whether anyone is there. Hinglish on purpose: it reads
# naturally under the hi-IN Bulbul voice for Hindi and English callers alike.
TURN_FAILED_MESSAGE = (
    "Sorry, mujhe thodi technical dikkat aa gayi. Kya aap please apni baat dobara bol sakte hain?"
)

# LiveKit's defaults (3 retries, 2s apart, 10s timeout) can leave a caller in
# ~30s of silence before an error surfaces. For a voice call it's better to
# fail fast and fall back / apologise.
SESSION_CONN_OPTIONS = SessionConnectOptions(
    stt_conn_options=APIConnectOptions(max_retry=2, retry_interval=0.5, timeout=5.0),
    llm_conn_options=APIConnectOptions(max_retry=1, retry_interval=0.5, timeout=5.0),
    tts_conn_options=APIConnectOptions(max_retry=2, retry_interval=0.5, timeout=5.0),
)


class Assistant(Agent):
    """Aisha. Business knowledge + tool-use rules live in the system prompt
    (see app/agent/prompts.py + the seeded Business row); this class only
    exposes the deterministic tool implementations."""

    def __init__(self, *, instructions: str) -> None:
        super().__init__(instructions=instructions)

    @function_tool()
    async def check_availability(
        self,
        context: RunContext[SessionData],
        date: str,
        doctor: str | None = None,
    ) -> dict:
        """Check real appointment availability at Sharma Dental Care for a given date.

        Args:
            date: ISO date to check, format YYYY-MM-DD.
            doctor: Optional doctor name to filter by (e.g. "Dr. Raj Sharma").
        """
        business_id = context.userdata.business_id

        def _query() -> dict:
            with session_scope() as session:
                return appointment_tools.check_availability(
                    session, business_id=business_id, date=date, doctor=doctor
                )

        result = await asyncio.to_thread(_query)
        logger.info("[tool] check_availability(%s, doctor=%s) -> %s", date, doctor, result)
        return result

    @function_tool()
    async def book_appointment(
        self,
        context: RunContext[SessionData],
        customer_name: str,
        customer_phone: str,
        date: str,
        time: str,
        service: str,
        doctor: str | None = None,
    ) -> dict:
        """Book a real appointment at Sharma Dental Care. Only call this after the caller
        has confirmed name, phone, service, date and time, and after check_availability
        has shown the slot as open.

        Args:
            customer_name: Caller's full name.
            customer_phone: Caller's phone number.
            date: ISO date YYYY-MM-DD.
            time: 24-hour time HH:MM.
            service: Service being booked, e.g. "Consultation" or "Teeth whitening".
            doctor: Optional preferred doctor's name.
        """
        business_id = context.userdata.business_id

        def _book() -> dict:
            with session_scope() as session:
                return appointment_tools.book_appointment(
                    session,
                    business_id=business_id,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    date=date,
                    time=time,
                    service=service,
                    doctor=doctor,
                )

        result = await asyncio.to_thread(_book)
        logger.info("[tool] book_appointment(%s) -> %s", customer_name, result)
        return result

    @function_tool()
    async def get_business_hours(
        self, context: RunContext[SessionData], day: str | None = None
    ) -> dict:
        """Get Sharma Dental Care's opening hours.

        Args:
            day: Optional single day name (e.g. "Saturday"). Omit for the full week.
        """
        business_id = context.userdata.business_id

        def _query() -> dict:
            with session_scope() as session:
                business = session.get(Business, business_id)
                return appointment_tools.get_business_hours(business, day)

        result = await asyncio.to_thread(_query)
        logger.info("[tool] get_business_hours(day=%s) -> %s", day, result)
        return result

    @function_tool()
    async def get_service_price(self, context: RunContext[SessionData], service: str) -> dict:
        """Get the price of a dental service at Sharma Dental Care.

        Args:
            service: Name of the service, e.g. "root canal".
        """
        business_id = context.userdata.business_id

        def _query() -> dict | None:
            with session_scope() as session:
                business = session.get(Business, business_id)
                return appointment_tools.get_service_price(business, service)

        result = await asyncio.to_thread(_query)
        logger.info("[tool] get_service_price(%s) -> %s", service, result)
        return result or {"error": f"No pricing information found for '{service}'."}


def prewarm(proc: JobProcess) -> None:
    # Loading the (local, ONNX) Silero VAD model is the one meaningfully slow
    # step, so it happens once per worker process instead of once per call.
    # sample_rate matches AUDIO_SAMPLE_RATE_IN -- see the comment above it.
    proc.userdata["vad"] = silero.VAD.load(sample_rate=AUDIO_SAMPLE_RATE_IN)


def _load_business_config(business_id: str) -> tuple[str, str, str]:
    """Sync DB read of everything the session needs up front: (system prompt,
    greeting, agent name). Run via `asyncio.to_thread` from the entrypoint."""
    with session_scope() as session:
        business = session.get(Business, business_id)
        if business is None:
            raise BusinessNotFoundError(f"No business with id={business_id!r}")
        instructions = build_system_prompt(business, render_business_knowledge(business))
        return instructions, business.greeting, business.agent_name


def _init_storage() -> None:
    ensure_dirs()
    init_db()


async def entrypoint(ctx: JobContext) -> None:
    settings = get_settings()
    business_id = settings.default_business_id

    # Everything below that touches SQLite runs in a worker thread: this event
    # loop also drives audio, VAD and turn detection, and the log showed
    # multi-hundred-ms stalls from synchronous work here.
    await asyncio.to_thread(_init_storage)
    (instructions, greeting, agent_name), _ = await asyncio.gather(
        asyncio.to_thread(_load_business_config, business_id),
        ctx.connect(),
    )

    call_id = await asyncio.to_thread(call_session.create_call, business_id, ctx.room.name)
    message_writer = call_session.CallMessageWriter(call_id)
    logger.info("[call %s] starting session for room=%s agent=%s", call_id, ctx.room.name, agent_name)

    session: AgentSession[SessionData] = AgentSession[SessionData](
        userdata=SessionData(business_id=business_id, call_id=call_id, room_name=ctx.room.name),
        stt=sarvam.STTRealtime(
            api_key=settings.sarvam_api_key,
            language=settings.sarvam_stt_language,
            mode="codemix",  # Hindi/English code-switching, e.g. Hinglish
            sample_rate=AUDIO_SAMPLE_RATE_IN,
        ),
        llm=get_agent_llm_with_fallback(settings),
        tts=sarvam.TTS(
            api_key=settings.sarvam_api_key,
            target_language_code=settings.sarvam_tts_language,
            speaker=settings.sarvam_tts_speaker,
            speech_sample_rate=AUDIO_SAMPLE_RATE_OUT,
        ),
        vad=ctx.proc.userdata["vad"],
        turn_detection="vad",
        allow_interruptions=True,
        conn_options=SESSION_CONN_OPTIONS,
    )

    # ---- Debug/demo logging: transcript, tool calls, latency metrics, turns ----

    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev) -> None:
        if ev.is_final:
            logger.info("[call %s] STT final: %r (lang=%s)", call_id, ev.transcript, ev.language)

    @session.on("conversation_item_added")
    def _on_item_added(ev) -> None:
        item = ev.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if role in ("user", "assistant") and text:
            message_writer.enqueue(role, text)
            logger.info("[call %s] %s: %s", call_id, role, text)

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        m = ev.metrics
        kind = getattr(m, "type", "unknown")
        if kind == "stt_metrics":
            logger.info("[call %s] latency stt duration=%.3fs", call_id, m.duration)
        elif kind == "llm_metrics":
            logger.info("[call %s] latency llm ttft=%.3fs duration=%.3fs", call_id, m.ttft, m.duration)
        elif kind == "tts_metrics":
            logger.info("[call %s] latency tts ttfb=%.3fs duration=%.3fs", call_id, m.ttfb, m.duration)
        elif kind == "eou_metrics":
            logger.info(
                "[call %s] latency end-of-turn delay=%.3fs transcription_delay=%.3fs",
                call_id,
                m.end_of_utterance_delay,
                m.transcription_delay,
            )

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        logger.info("[call %s] agent_state: %s -> %s", call_id, ev.old_state, ev.new_state)

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        logger.info("[call %s] user_state: %s -> %s", call_id, ev.old_state, ev.new_state)

    @session.on("agent_false_interruption")
    def _on_false_interruption(ev) -> None:
        logger.info("[call %s] false interruption detected (resumed=%s)", call_id, ev.resumed)

    @session.on("error")
    def _on_error(ev) -> None:
        # Never crash the worker over a transient STT/LLM/TTS provider error.
        logger.error("[call %s] session error from %s: %s", call_id, ev.source, ev.error)
        error = ev.error
        if getattr(error, "recoverable", True):
            return  # the framework is retrying; nothing was lost yet
        # An unrecoverable LLM/STT error means this turn produced no reply.
        # TTS still works, so tell the caller instead of going silent. (For a
        # TTS failure there's no voice to apologise with; the session closes
        # after repeated failures and _on_close ends the call.)
        if getattr(error, "type", None) in ("llm_error", "stt_error"):
            try:
                session.say(TURN_FAILED_MESSAGE, allow_interruptions=True, add_to_chat_ctx=False)
            except RuntimeError:
                pass  # session already closing

    call_status = {"value": "completed"}

    @session.on("close")
    def _on_close(ev) -> None:
        call_status["value"] = "failed" if ev.error else "completed"
        if ev.error:
            # The session gave up after repeated provider failures. End the job
            # so the agent leaves the room and the caller's UI sees the
            # disconnect, rather than sitting in a silent room.
            logger.error("[call %s] session closed on error; ending call", call_id)
            ctx.shutdown(reason="session error")

    async def _on_shutdown() -> None:
        # Flush pending transcript writes before closing the call out, so the
        # post-call extraction sees the complete transcript.
        await message_writer.aclose()
        await asyncio.to_thread(call_session.end_call, call_id, call_status["value"])
        try:
            await run_post_call_extraction(call_id)
        except Exception:
            logger.exception("[call %s] post-call extraction crashed", call_id)

    ctx.add_shutdown_callback(_on_shutdown)

    await session.start(
        agent=Assistant(instructions=instructions),
        room=ctx.room,
        room_input_options=room_io.RoomInputOptions(audio_sample_rate=AUDIO_SAMPLE_RATE_IN),
        room_output_options=room_io.RoomOutputOptions(audio_sample_rate=AUDIO_SAMPLE_RATE_OUT),
    )
    await session.generate_reply(
        instructions=f'Greet the caller now. Say almost exactly: "{greeting}"'
    )


if __name__ == "__main__":
    settings = get_settings()
    missing = settings.missing_credentials()
    if missing:
        logger.warning(
            "Missing/placeholder credentials: %s. The worker will start but will fail to "
            "connect/process calls until real values are set in .env.",
            ", ".join(missing),
        )
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            ws_url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
    )
