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
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    room_io,
)
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
from app.business.service import DatabaseKnowledgeProvider  # noqa: E402
from app.llm.provider import get_llm_provider  # noqa: E402
from app.postcall.extractor import run_post_call_extraction  # noqa: E402
from app.tools import appointments as appointment_tools  # noqa: E402

logger = logging.getLogger("voice_agent.worker")


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
        with session_scope() as session:
            result = appointment_tools.check_availability(
                session, business_id=business_id, date=date, doctor=doctor
            )
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
        with session_scope() as session:
            result = appointment_tools.book_appointment(
                session,
                business_id=business_id,
                customer_name=customer_name,
                customer_phone=customer_phone,
                date=date,
                time=time,
                service=service,
                doctor=doctor,
            )
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
        with session_scope() as session:
            business = session.get(Business, context.userdata.business_id)
            result = appointment_tools.get_business_hours(business, day)
        logger.info("[tool] get_business_hours(day=%s) -> %s", day, result)
        return result

    @function_tool()
    async def get_service_price(self, context: RunContext[SessionData], service: str) -> dict:
        """Get the price of a dental service at Sharma Dental Care.

        Args:
            service: Name of the service, e.g. "root canal".
        """
        with session_scope() as session:
            business = session.get(Business, context.userdata.business_id)
            result = appointment_tools.get_service_price(business, service)
        logger.info("[tool] get_service_price(%s) -> %s", service, result)
        return result or {"error": f"No pricing information found for '{service}'."}


def prewarm(proc: JobProcess) -> None:
    # Loading the (local, ONNX) Silero VAD model is the one meaningfully slow
    # step, so it happens once per worker process instead of once per call.
    # sample_rate matches AUDIO_SAMPLE_RATE_IN -- see the comment above it.
    proc.userdata["vad"] = silero.VAD.load(sample_rate=AUDIO_SAMPLE_RATE_IN)

    # Two lazy-import stalls measured live, both moved here so they land during
    # process warm-up instead of during a real call's first turn:
    # 1. livekit-plugins-openai's LLM (used for BOTH the "openai" and "groq"
    #    providers -- see app/llm/provider.py, Groq just points the same
    #    plugin at a different base_url) prewarms itself via client.models.list(),
    #    which on first use imports openai's `resources.beta.chatkit` submodule
    #    tree -- measured at ~2.9s blocking the asyncio event loop.
    # 2. Constructing any AsyncOpenAI client (e.g. GroqProvider's own client for
    #    post-call extraction, in get_llm_provider() below) imports httpcore's
    #    sync backend the first time -- measured at ~0.5s.
    # Both block audio/turn handling for their whole duration wherever they land;
    # importing them up front makes that a one-time per-process cost instead of
    # a per-call one.
    import httpcore  # noqa: F401
    import openai.resources.beta.chatkit.chatkit  # noqa: F401


async def entrypoint(ctx: JobContext) -> None:
    ensure_dirs()
    init_db()
    settings = get_settings()

    await ctx.connect()

    business_id = settings.default_business_id
    with session_scope() as session:
        provider = DatabaseKnowledgeProvider(session)
        business = await provider.get_business(business_id)
        knowledge_context = await provider.get_context(business_id)
        instructions = build_system_prompt(business, knowledge_context)
        greeting = business.greeting
        agent_name = business.agent_name

    # create_call/add_call_message/end_call each run a synchronous SQLAlchemy
    # commit -- measured live (agent_worker_verify.log) at 130-570ms apiece,
    # which blocks this same asyncio loop that also handles audio and turn
    # detection ("event loop blocked" warnings from livekit-agents pointed
    # straight at sqlalchemy's do_commit). Running them in a worker thread
    # keeps the DB write off the realtime path instead of just moving where
    # in the call it stalls.
    call_id = await asyncio.to_thread(call_session.create_call, business_id, room_name=ctx.room.name)
    logger.info("[call %s] starting session for room=%s agent=%s", call_id, ctx.room.name, agent_name)

    llm_provider = get_llm_provider(settings)

    session: AgentSession[SessionData] = AgentSession[SessionData](
        userdata=SessionData(business_id=business_id, call_id=call_id, room_name=ctx.room.name),
        stt=sarvam.STTRealtime(
            api_key=settings.sarvam_api_key,
            # `language` was previously pinned to a fixed BCP-47 code (hi-IN), which
            # doesn't just set a *default* -- it forces Sarvam to decode every
            # utterance through that language's model. A pinned "hi-IN" was
            # confirmed live to mis-transcribe/drop words in English speech (being
            # decoded through a Hindi acoustic/language model) and to report every
            # utterance's language as "hi" regardless of what was actually said --
            # which then fed a Hindi (or garbled) transcript to the LLM, whose
            # "reply in the caller's language" instruction faithfully mirrored that
            # mistake back in Hindi even when the caller spoke English. `"auto"` is
            # a first-class value here (see livekit-plugins-sarvam's
            # RealtimeSTTOptions.language / SUPPORTED_LANGUAGES) that runs Sarvam's
            # own per-utterance language identification instead, so English and
            # Hindi/Hinglish speech are each decoded through their own model and
            # `ev.language` on user_input_transcribed reports what was actually
            # detected -- which is also what the TTS-retargeting handler below
            # already relies on.
            language=settings.sarvam_stt_language,
            # "codemix" was separately confirmed to render pure-English utterances
            # as Devanagari-script phonetic transliteration (e.g. "Hi Aisha, how are
            # you?" -> "हाय आयशा, हाउ आर यू?") even independent of the language-pin
            # issue above. "transcribe" is standard transcription in the language
            # actually spoken/detected, which is what we want; "codemix" is for
            # genuinely mixed Hindi/English *within one utterance*, not a wholesale
            # script choice.
            mode="transcribe",
            sample_rate=AUDIO_SAMPLE_RATE_IN,
        ),
        llm=llm_provider.get_agent_llm(),
        tts=sarvam.TTS(
            api_key=settings.sarvam_api_key,
            target_language_code=settings.sarvam_tts_language,
            speaker=settings.sarvam_tts_speaker,
            speech_sample_rate=AUDIO_SAMPLE_RATE_OUT,
        ),
        # `vad` still gates raw speech/silence framing (and is required by the STT/TTS
        # provider interfaces below), but deliberately NOT passed as `turn_detection`:
        # livekit-agents 1.8's default turn detector is a semantic end-of-turn model
        # (inference.TurnDetector, per-language thresholds incl. "hi") that judges
        # whether a pause is really turn-final instead of just timing raw silence.
        # Forcing turn_detection="vad" (the previous config) disables that model and
        # falls back to a fixed silence timeout -- worse latency on confident turn
        # ends AND more false interruptions on mid-sentence pauses, which are exactly
        # this demo's two headline risks for Hindi/Hinglish speech. Leaving
        # `turn_detection` unset lets AgentSession pick that smart default.
        vad=ctx.proc.userdata["vad"],
        # Interruption handling: the framework's default "adaptive" mode (ML-based,
        # tolerates likely backchannel like "haan"/"hmm" near the start of a turn so
        # it doesn't cut the agent off for those) let a real test caller's barge-in
        # go unrecognized -- their speech reached the transcript but Aisha kept
        # talking to the end of her sentence anyway, because the classifier scored
        # it below its interruption threshold. Switching to "vad" mode makes ANY
        # sustained user speech stop Aisha immediately, no semantic judgment call --
        # matches "the instant the customer starts speaking, pause and listen."
        # Trade-off: short backchannel utterances will now interrupt her too, since
        # vad mode can't distinguish them from real barge-in the way adaptive does.
        turn_handling={"interruption": {"mode": "vad", "min_duration": 0.3}},
    )

    # ---- Debug/demo logging: transcript, tool calls, latency metrics, turns ----

    # Bulbul's target_language_code is set once at TTS construction (hi-IN, above)
    # but the caller's actual language varies turn by turn -- a real test call
    # showed Bulbul mispronouncing plain English words ("I'm" -> "Im", "Wednesday")
    # when synthesizing an English reply under a Hindi target. Sarvam's realtime STT
    # reports a detected `language` per final transcript (already logged below);
    # retarget the TTS to match it before the next reply is generated, using
    # sarvam.TTS.update_options (a real runtime API, not a full reconnect). Only
    # "en" and "hi" are mapped since that's this demo's supported range -- anything
    # else (misdetection, silence) is left on whatever language is already active
    # rather than guessed at.
    _stt_lang_to_tts_target = {"en": "en-IN", "hi": "hi-IN"}
    _last_tts_target = {"value": settings.sarvam_tts_language}

    def _persist_call_message_async(role: str, text: str) -> None:
        """conversation_item_added fires on every turn and must stay a plain
        sync callback (it's invoked directly by livekit.rtc's event emitter),
        so the DB commit is offloaded to a worker thread and fire-and-forgotten
        rather than awaited -- see the create_call comment above for why a
        commit on this loop is expensive."""

        task = asyncio.create_task(
            asyncio.to_thread(call_session.add_call_message, call_id, role=role, text=text)
        )

        def _log_if_failed(t: asyncio.Task) -> None:
            exc = t.exception()
            if exc:
                logger.error("[call %s] failed to persist %s message: %s", call_id, role, exc)

        task.add_done_callback(_log_if_failed)

    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev) -> None:
        if ev.is_final:
            logger.info("[call %s] STT final: %r (lang=%s)", call_id, ev.transcript, ev.language)
            target = ev.language and _stt_lang_to_tts_target.get(ev.language.language)
            if target and target != _last_tts_target["value"]:
                _last_tts_target["value"] = target
                session.tts.update_options(target_language_code=target)
                logger.info("[call %s] TTS target_language_code -> %s", call_id, target)

    @session.on("conversation_item_added")
    def _on_item_added(ev) -> None:
        item = ev.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if role in ("user", "assistant") and text:
            _persist_call_message_async(role, text)
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

    call_status = {"value": "completed"}

    @session.on("close")
    def _on_close(ev) -> None:
        call_status["value"] = "failed" if ev.error else "completed"

    async def _on_shutdown() -> None:
        await asyncio.to_thread(call_session.end_call, call_id, status=call_status["value"])
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
