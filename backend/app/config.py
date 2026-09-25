"""Central application configuration.

All secrets and environment-specific values live in `.env` (see `.env.example`).
Nothing here should ever contain a real credential.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
LOGS_DIR = BACKEND_DIR / "logs"

# Values that mean "the developer hasn't filled this in yet".
_PLACEHOLDER_MARKERS = {
    "",
    "YOUR_LIVEKIT_URL",
    "YOUR_LIVEKIT_API_KEY",
    "YOUR_LIVEKIT_API_SECRET",
    "YOUR_SARVAM_API_KEY",
    "YOUR_OPENAI_API_KEY",
    "wss://YOUR_LIVEKIT_URL",
    "CHANGE_ME",
}


class Settings(BaseSettings):
    """Typed application settings, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LiveKit ---
    livekit_url: str = Field(default="wss://YOUR_LIVEKIT_URL", alias="LIVEKIT_URL")
    livekit_api_key: str = Field(default="YOUR_LIVEKIT_API_KEY", alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(default="YOUR_LIVEKIT_API_SECRET", alias="LIVEKIT_API_SECRET")

    # --- Sarvam AI (STT: Saaras realtime, TTS: Bulbul streaming) ---
    sarvam_api_key: str = Field(default="YOUR_SARVAM_API_KEY", alias="SARVAM_API_KEY")
    sarvam_stt_language: str = Field(default="hi-IN", alias="SARVAM_STT_LANGUAGE")
    sarvam_tts_language: str = Field(default="hi-IN", alias="SARVAM_TTS_LANGUAGE")
    sarvam_tts_speaker: str = Field(default="pooja", alias="SARVAM_TTS_SPEAKER")
    # Used only when LLM_PROVIDER=sarvam -- reuses SARVAM_API_KEY, no separate credential.
    sarvam_llm_model: str = Field(default="sarvam-105b", alias="SARVAM_LLM_MODEL")

    # --- LLM provider abstraction ---
    # "openai" (needs OPENAI_API_KEY + billing), "sarvam" (reuses SARVAM_API_KEY,
    # but its Chat Completions API is beta-gated per-account), or "groq" (free
    # tier, needs GROQ_API_KEY from console.groq.com). See app/llm/provider.py.
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    # Comma-separated providers to fail over to, in order, when LLM_PROVIDER
    # errors or times out mid-call (e.g. "groq,sarvam"). Providers whose
    # credentials are still placeholders are skipped. Empty = no fallback.
    llm_fallback_providers: str = Field(default="", alias="LLM_FALLBACK_PROVIDERS")
    # Per-attempt budget before the LLM FallbackAdapter moves to the next
    # provider. Keep this short: the caller is sitting in silence meanwhile.
    llm_attempt_timeout: float = Field(default=4.0, alias="LLM_ATTEMPT_TIMEOUT")
    openai_api_key: str = Field(default="YOUR_OPENAI_API_KEY", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_extraction_model: str = Field(default="gpt-4o-mini", alias="OPENAI_EXTRACTION_MODEL")
    groq_api_key: str = Field(default="YOUR_GROQ_API_KEY", alias="GROQ_API_KEY")
    # Groq's exact model lineup varies by account/region -- if this 404s,
    # run `GET https://api.groq.com/openai/v1/models` with your own key to
    # see what's actually available and update this.
    groq_model: str = Field(default="openai/gpt-oss-20b", alias="GROQ_MODEL")

    # --- Agent worker ---
    # Pre-started, prewarmed job processes kept waiting for the next call.
    # LiveKit's dev-mode default is 0, which means every call pays process
    # start + imports + VAD load before the agent can join ("no warmed
    # process available for job" in the log). Each idle process costs RAM
    # (roughly 200-400MB with Silero loaded); size this to expected
    # concurrent call bursts.
    agent_idle_processes: int = Field(default=1, alias="AGENT_IDLE_PROCESSES")

    # --- Storage ---
    database_url: str = Field(default="sqlite:///./data/voice_agent.db", alias="DATABASE_URL")

    # --- App ---
    default_business_id: str = Field(default="sharma-dental", alias="DEFAULT_BUSINESS_ID")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- API server ---
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")

    @field_validator("database_url")
    @classmethod
    def _resolve_sqlite_path(cls, v: str) -> str:
        """Rewrite relative sqlite paths so they always resolve to backend/data,
        regardless of the process's current working directory."""
        prefix = "sqlite:///./"
        if v.startswith(prefix):
            relative = v[len(prefix) :]
            absolute = (BACKEND_DIR / relative).resolve()
            return f"sqlite:///{absolute.as_posix()}"
        return v

    @property
    def llm_fallback_provider_list(self) -> list[str]:
        return [p.strip().lower() for p in self.llm_fallback_providers.split(",") if p.strip()]

    def provider_has_credentials(self, provider: str) -> bool:
        key = {
            "openai": self.openai_api_key,
            "groq": self.groq_api_key,
            "sarvam": self.sarvam_api_key,
        }.get(provider.lower())
        return key is not None and key not in _PLACEHOLDER_MARKERS and not key.startswith("YOUR_")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def missing_credentials(self) -> list[str]:
        """Return a human-readable list of credentials that still look like placeholders."""
        missing = []
        checks = {
            "LIVEKIT_URL": self.livekit_url,
            "LIVEKIT_API_KEY": self.livekit_api_key,
            "LIVEKIT_API_SECRET": self.livekit_api_secret,
            "SARVAM_API_KEY": self.sarvam_api_key,
        }
        if self.llm_provider == "openai":
            checks["OPENAI_API_KEY"] = self.openai_api_key
        elif self.llm_provider == "groq":
            checks["GROQ_API_KEY"] = self.groq_api_key

        for name, value in checks.items():
            if value in _PLACEHOLDER_MARKERS or value.startswith("YOUR_"):
                missing.append(name)
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
