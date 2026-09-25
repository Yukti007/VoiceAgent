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
