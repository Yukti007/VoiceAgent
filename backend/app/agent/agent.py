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
from collections.abc import AsyncIterable, AsyncIterator

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
    ModelSettings,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    room_io,
)
from livekit.agents import inference  # noqa: E402
from livekit.agents.voice.agent_session import SessionConnectOptions  # noqa: E402
from livekit.plugins import noise_cancellation, sarvam, silero  # noqa: E402

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

from app.agent import audio_cache  # noqa: E402
from app.agent import session as call_session  # noqa: E402
from app.agent.prompts import build_system_prompt  # noqa: E402
from app.agent.state import SessionData  # noqa: E402
from app.business.service import BusinessNotFoundError, render_business_knowledge  # noqa: E402
from app.database.seed import ensure_availability  # noqa: E402
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

# Spoken only if a tool is still running after FILLER_DELAY seconds of
# silence, so the caller knows Aisha is working rather than hearing dead air.
# Fast tool calls never trigger them.
FILLER_DELAY = 0.8
AVAILABILITY_FILLER = "Ek second, main availability check kar rahi hoon."
BOOKING_FILLER = "Ek moment, main aapki booking confirm kar rahi hoon."

# LiveKit's defaults (3 retries, 2s apart, 10s timeout) can leave a caller in
# ~30s of silence before an error surfaces. For a voice call it's better to
# fail fast and fall back / apologise.
SESSION_CONN_OPTIONS = SessionConnectOptions(
    stt_conn_options=APIConnectOptions(max_retry=2, retry_interval=0.5, timeout=5.0),
    llm_conn_options=APIConnectOptions(max_retry=1, retry_interval=0.5, timeout=5.0),
    tts_conn_options=APIConnectOptions(max_retry=2, retry_interval=0.5, timeout=5.0),
)


# How much of a reply to read before choosing the TTS language for it.
_TTS_LANGUAGE_PEEK_LETTERS = 12


def tts_language_for(text: str) -> str | None:
    """Bulbul target language for a reply, from its script: Devanagari ->
    hi-IN, Latin -> en-IN, None if there are no letters to judge by yet."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    devanagari = sum(1 for c in letters if "ऀ" <= c <= "ॿ")
    return "hi-IN" if devanagari * 2 >= len(letters) else "en-IN"


class Assistant(Agent):
    """Aisha. Business knowledge + tool-use rules live in the system prompt
    (see app/agent/prompts.py + the seeded Business row); this class only
    exposes the deterministic tool implementations."""

    def __init__(self, *, instructions: str) -> None:
        super().__init__(instructions=instructions)

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        """Voice each reply in the language it is written in.

        The TTS language used to follow the caller's last utterance, so a caller
        speaking Hinglish in Devanagari got English replies read with a Hindi
        voice -- phone numbers came out as Hindi numerals ("battees bayalees")
        even when the caller asked for English. Peek at the start of the reply,
        pick the language from its script, then synthesize as normal."""
        head: list[str] = []
        stream = text.__aiter__()
        async for chunk in stream:
            head.append(chunk)
            if sum(c.isalpha() for c in "".join(head)) >= _TTS_LANGUAGE_PEEK_LETTERS:
                break

        target = tts_language_for("".join(head))
        tts = self.session.tts
        if target and tts is not None and getattr(tts, "_opts", None) is not None:
            if tts._opts.target_language_code != target:
                tts.update_options(target_language_code=target)
                logger.info("TTS target_language_code -> %s", target)

        async def _replay() -> AsyncIterator[str]:
            for chunk in head:
                yield chunk
            async for chunk in stream:
                yield chunk

        async for frame in Agent.default.tts_node(self, _replay(), model_settings):
            yield frame

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

        async with context.with_filler(AVAILABILITY_FILLER, delay=FILLER_DELAY):
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
        # Critical section: once the booking is being written, a barge-in must
        # not cancel the speech that confirms it -- otherwise the slot is
        # booked but the caller never hears it and may try to book again.
        try:
            context.disallow_interruptions()
        except RuntimeError:
            # The caller already interrupted before we started; don't book on
            # a turn they talked over.
            return {
                "success": False,
                "error": "Not booked: the caller interrupted. Confirm the details with them again.",
            }

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

        async with context.with_filler(BOOKING_FILLER, delay=FILLER_DELAY):
            result = await asyncio.to_thread(_book)
        logger.info("[tool] book_appointment(%s) -> %s", customer_name, result)
        return result

    @function_tool()
    async def book_group_appointment(
        self,
        context: RunContext[SessionData],
        patient_names: list[str],
        customer_phone: str,
        date: str,
        time: str,
        service: str,
    ) -> dict:
        """Book the same date and time for several people at once, e.g. a caller and
        their child. Use instead of book_appointment whenever more than one person
        needs an appointment. Books everyone or no one.

        Args:
            patient_names: Full name of each person to book, including the caller if
                they are one of the patients.
            customer_phone: Contact phone number shared by the group.
            date: ISO date YYYY-MM-DD.
            time: 24-hour time HH:MM.
            service: Service being booked, e.g. "Teeth cleaning".
        """
        try:
            context.disallow_interruptions()
        except RuntimeError:
            return {
                "success": False,
                "error": "Not booked: the caller interrupted. Confirm the details with them again.",
            }

        business_id = context.userdata.business_id

        def _book() -> dict:
            with session_scope() as session:
                return appointment_tools.book_group_appointment(
                    session,
                    business_id=business_id,
                    customer_phone=customer_phone,
                    patient_names=patient_names,
                    date=date,
                    time=time,
                    service=service,
                )

        async with context.with_filler(BOOKING_FILLER, delay=FILLER_DELAY):
            result = await asyncio.to_thread(_book)
        logger.info("[tool] book_group_appointment(%s) -> %s", patient_names, result)
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
    """Runs once in each idle job process, before any call is assigned to it.

    Everything slow that doesn't depend on the specific call belongs here, so
    the call itself only pays for connecting to the room.
    """
    # Loading the (local, ONNX) Silero VAD model is the slowest step.
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

    # The openai SDK imports most of its type modules lazily, on first use.
    # agent_worker.log showed that first use happening mid-call, on the event
    # loop ("event loop blocked for 1996ms importing openai.types.beta..."),
    # plus anyio's stream module. Import them now instead.
    import anyio._core._streams  # noqa: F401
    import openai.resources  # noqa: F401
    import openai.types.beta  # noqa: F401
    import openai.types.chat  # noqa: F401
    import livekit.plugins.openai.llm  # noqa: F401

    # Create the SQLite engine, connection pool and tables once per process
    # rather than at the start of every call.
    _init_storage()
    proc.userdata["storage_ready"] = True


def _load_business_config(business_id: str) -> tuple[str, str, str, str]:
    """Sync DB read of everything the session needs up front: (system prompt,
    greeting, agent name, STT vocabulary hint). Run via `asyncio.to_thread`
    from the entrypoint."""
    with session_scope() as session:
        business = session.get(Business, business_id)
        if business is None:
            raise BusinessNotFoundError(f"No business with id={business_id!r}")
        ensure_availability(session, business_id)
        instructions = build_system_prompt(business, render_business_knowledge(business))
        # Sarvam's realtime STT takes a `prompt` that biases decoding toward words
        # it has no prior on: a live call heard "Hi Aisha" as "I'm Ayesha".
        knowledge = business.business_knowledge or {}
        service_names = [s.get("name") for s in knowledge.get("services", []) if s.get("name")]
        vocabulary_hint = ", ".join(
            [business.agent_name, business.name, *knowledge.get("doctors", []), *service_names]
        )
        return instructions, business.greeting, business.agent_name, vocabulary_hint


def _init_storage() -> None:
    ensure_dirs()
    init_db()


def _build_turn_handling(settings) -> dict:
    """Turn-taking config (LiveKit's TurnHandlingOptions, as a plain dict).

    Silence-only VAD forces a bad trade-off: a short silence threshold cuts
    Hindi/Hinglish speakers off at natural mid-sentence pauses, and a long one
    adds that delay to every single turn. The audio turn-detection model lets
    the turn end quickly (min_delay) when the caller is clearly done, and
    waits up to max_delay only when they sound mid-thought.
    """
    turn_detection = (
        inference.TurnDetector(sample_rate=AUDIO_SAMPLE_RATE_IN)
        if settings.turn_detection.lower() == "model"
        else "vad"
    )
    return {
        "turn_detection": turn_detection,
        "endpointing": {
            "min_delay": settings.endpointing_min_delay,
            "max_delay": settings.endpointing_max_delay,
        },
        "interruption": _build_interruption_options(settings),
        "preemptive_generation": {"enabled": settings.preemptive_generation},
    }


def _build_interruption_options(settings) -> dict:
    """Barge-in config (LiveKit's InterruptionOptions).

    Framework defaults stop Aisha for any detected speech, so an "haan",
    "hmm" or background noise cut her off mid-sentence. Here the adaptive
    detector filters backchannels, a minimum duration filters coughs and
    clicks, and a false interruption (no words follow) resumes her speech
    after a short pause instead of leaving the turn dropped.
    """
    mode = settings.interruption_mode.lower()
    min_words = settings.interruption_min_words
    if min_words is None:
        min_words = 0 if mode == "adaptive" else 2
    return {
        "enabled": True,
        "mode": mode,
        "min_duration": settings.interruption_min_duration,
        "min_words": min_words,
        "resume_false_interruption": True,
        "false_interruption_timeout": settings.false_interruption_timeout,
    }


def _build_noise_cancellation(settings):
    mode = settings.noise_cancellation.lower()
    if mode == "bvc":
        return noise_cancellation.BVC()
    if mode == "nc":
        return noise_cancellation.NC()
    return None


async def entrypoint(ctx: JobContext) -> None:
    settings = get_settings()
    business_id = settings.default_business_id

    # Everything below that touches SQLite runs in a worker thread: this event
    # loop also drives audio, VAD and turn detection, and the log showed
    # multi-hundred-ms stalls from synchronous work here.
    if not ctx.proc.userdata.get("storage_ready"):
        await asyncio.to_thread(_init_storage)
    (instructions, greeting, agent_name, stt_vocabulary_hint), _ = await asyncio.gather(
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
            prompt=stt_vocabulary_hint,
        ),
        llm=get_agent_llm_with_fallback(settings),
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
        turn_handling=_build_turn_handling(settings),
        conn_options=SESSION_CONN_OPTIONS,
    )

    # ---- Debug/demo logging: transcript, tool calls, latency metrics, turns ----

    # The TTS language is chosen per reply from the reply's own script -- see
    # Assistant.tts_node -- not from the caller's detected language.
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
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                sample_rate=AUDIO_SAMPLE_RATE_IN,
                noise_cancellation=_build_noise_cancellation(settings),
            ),
            audio_output=room_io.AudioOutputOptions(sample_rate=AUDIO_SAMPLE_RATE_OUT),
        ),
    )
    await _say_greeting(session, greeting, settings)


async def _say_greeting(session: AgentSession, greeting: str, settings) -> None:
    """Speak the fixed greeting without an LLM round-trip.

    Previously this was `generate_reply(instructions=...)`, which paid a full
    LLM time-to-first-token before the first word (and could paraphrase the
    greeting). `say()` goes straight to TTS; with a cache hit it skips TTS too
    and starts streaming audio immediately.
    """
    key = audio_cache.cache_key(
        greeting,
        speaker=settings.sarvam_tts_speaker,
        language=settings.sarvam_tts_language,
        sample_rate=AUDIO_SAMPLE_RATE_OUT,
    )
    cached = await asyncio.to_thread(audio_cache.load, key)
    if cached is not None:
        pcm, sample_rate, num_channels = cached
        session.say(
            greeting,
            audio=audio_cache.frames_from_pcm(pcm, sample_rate, num_channels),
            allow_interruptions=False,
        )
        return

    # Not interruptible: background noise or an "hello?" in the first second
    # used to cancel the greeting outright, leaving the caller in silence.
    handle = session.say(greeting, allow_interruptions=False)

    async def _warm_cache() -> None:
        # After the live greeting has played, so the extra synthesis never
        # competes with it. One-time cost per greeting/voice per machine.
        await handle.wait_for_playout()
        await audio_cache.synthesize_and_save(session.tts, greeting, key)

    asyncio.create_task(_warm_cache(), name="warm-greeting-cache")


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
            num_idle_processes=settings.agent_idle_processes,
            # prewarm now also does imports + DB init; give slow machines
            # (the Windows dev box in the logs) room before it's considered hung.
            initialize_process_timeout=30.0,
        )
    )
