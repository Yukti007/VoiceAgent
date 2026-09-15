"""Test configuration: points the whole app at an isolated, throwaway SQLite
file (never the developer's real backend/data/voice_agent.db) and seeds it
once per test session."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="voice_agent_test_")
_TEST_DB_PATH = Path(_tmp_dir, "test.db").as_posix()

# Must be set before any `app.*` module is imported, since app.config.Settings
# reads the environment at import/first-use time.
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ.setdefault("LIVEKIT_URL", "wss://test.invalid")
os.environ.setdefault("LIVEKIT_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-secret")
os.environ.setdefault("SARVAM_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DEFAULT_BUSINESS_ID", "sharma-dental")

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database.database import init_db  # noqa: E402
from app.database.seed import seed  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    init_db()
    seed()
    yield


@pytest.fixture
def business_id() -> str:
    return get_settings().default_business_id
