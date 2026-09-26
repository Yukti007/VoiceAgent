from livekit.agents import AgentSession, inference

from app.agent.agent import _build_turn_handling
from app.config import Settings


def _settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


async def test_model_turn_detection_is_default_and_accepted_by_session():
    turn_handling = _build_turn_handling(_settings())
    assert isinstance(turn_handling["turn_detection"], inference.TurnDetector)
    assert turn_handling["endpointing"] == {"min_delay": 0.3, "max_delay": 2.5}
    assert turn_handling["preemptive_generation"] == {"enabled": True}
    # Must be a valid TurnHandlingOptions for this livekit-agents version.
    AgentSession(turn_handling=turn_handling)


async def test_vad_mode_remains_available():
    turn_handling = _build_turn_handling(
        _settings(TURN_DETECTION="vad", ENDPOINTING_MIN_DELAY="0.5", PREEMPTIVE_GENERATION="false")
    )
    assert turn_handling["turn_detection"] == "vad"
    assert turn_handling["endpointing"]["min_delay"] == 0.5
    assert turn_handling["preemptive_generation"] == {"enabled": False}
    AgentSession(turn_handling=turn_handling)


def test_interruption_defaults_filter_backchannels_and_resume_quickly():
    from app.agent.agent import _build_interruption_options

    adaptive = _build_interruption_options(_settings(INTERRUPTION_MIN_WORDS=""))
    assert adaptive["mode"] == "adaptive"
    assert adaptive["min_words"] == 0  # a single "ruko" must still stop Aisha
    assert adaptive["resume_false_interruption"] is True
    assert adaptive["false_interruption_timeout"] == 1.0

    vad = _build_interruption_options(_settings(INTERRUPTION_MODE="vad"))
    assert vad["min_words"] == 2  # only guard against "haan"/"hmm" without the model

    explicit = _build_interruption_options(_settings(INTERRUPTION_MIN_WORDS="3"))
    assert explicit["min_words"] == 3


async def test_full_turn_handling_is_accepted_by_session():
    AgentSession(turn_handling=_build_turn_handling(_settings()))


def test_noise_cancellation_modes():
    from app.agent.agent import _build_noise_cancellation

    assert _build_noise_cancellation(_settings(NOISE_CANCELLATION="off")) is None
    assert _build_noise_cancellation(_settings(NOISE_CANCELLATION="bvc")) is not None
    assert _build_noise_cancellation(_settings(NOISE_CANCELLATION="nc")) is not None
