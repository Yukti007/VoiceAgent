from app.config import Settings
from app.llm.provider import get_agent_llm_with_fallback, resolve_provider_chain


def _settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


def test_chain_is_primary_only_by_default():
    s = _settings(LLM_PROVIDER="openai", OPENAI_API_KEY="k")
    assert resolve_provider_chain(s) == ["openai"]


def test_chain_skips_duplicates_and_providers_without_credentials():
    s = _settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="k",
        GROQ_API_KEY="YOUR_GROQ_API_KEY",  # placeholder -> skipped
        SARVAM_API_KEY="k",
        LLM_FALLBACK_PROVIDERS="openai, groq, Sarvam",
    )
    assert resolve_provider_chain(s) == ["openai", "sarvam"]


def test_fallback_adapter_wraps_multiple_providers():
    from livekit.agents import llm as lk_llm

    s = _settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="k",
        GROQ_API_KEY="g",
        LLM_FALLBACK_PROVIDERS="groq",
    )
    assert isinstance(get_agent_llm_with_fallback(s), lk_llm.FallbackAdapter)

    single = _settings(LLM_PROVIDER="openai", OPENAI_API_KEY="k")
    assert not isinstance(get_agent_llm_with_fallback(single), lk_llm.FallbackAdapter)
