"""Conversation evals: scripted caller turns against the real agent (real LLM
from .env, real tools) in LiveKit's text mode, checked against the database.

They cost LLM tokens and need network, so they only run when asked:

    RUN_EVALS=1 .venv/Scripts/python.exe -m pytest evals -v      (Windows)
    RUN_EVALS=1 .venv/bin/python -m pytest evals -v              (macOS/Linux)

Each run writes evals/results/latest.md (pass/fail table) and one transcript
per scenario under evals/results/transcripts/.
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="voice_agent_eval_")
# Must be set before any `app.*` import. Credentials come from the real .env.
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp_dir, 'eval.db').as_posix()}"

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

RESULTS_DIR = Path(__file__).parent / "results"
_outcomes: dict[str, dict] = {}


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_EVALS") == "1":
        return
    skip = pytest.mark.skip(reason="conversation evals call a real LLM; set RUN_EVALS=1 to run them")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _eval_database():
    from app.database.database import init_db
    from app.database.seed import seed

    init_db()
    seed()
    yield


@pytest.fixture
def business_id() -> str:
    return get_settings().default_business_id


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        doc = (item.function.__doc__ or item.name).strip().splitlines()[0]
        outcome = "skipped" if report.skipped else report.outcome
        reason = str(report.longrepr).strip().splitlines()[-1][:160] if report.failed else ""
        # The LLM provider refusing requests (quota, outage) says nothing about
        # the agent's behaviour: report it separately from a real failure.
        if report.failed and call.excinfo is not None and call.excinfo.typename == "APIConnectionError":
            outcome, reason = "not run", "LLM provider unavailable (e.g. rate or quota limit)"
        _outcomes[item.name] = {"scenario": doc, "outcome": outcome, "seconds": report.duration, "reason": reason}


def pytest_sessionfinish(session, exitstatus):
    ran = {k: v for k, v in _outcomes.items() if v["outcome"] != "skipped"}
    if not ran:
        return
    settings = get_settings()
    provider = settings.llm_provider
    model = {"openai": settings.openai_model, "groq": settings.groq_model, "sarvam": settings.sarvam_llm_model}.get(
        provider, "?"
    )
    passed = sum(v["outcome"] == "passed" for v in ran.values())
    completed = sum(v["outcome"] != "not run" for v in ran.values())
    lines = [
        "# Conversation eval results",
        "",
        f"- Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- LLM: {provider} / {model}",
        f"- Passed: **{passed} / {completed}** completed scenarios"
        + (f" ({len(ran) - completed} not run: LLM provider unavailable)" if completed < len(ran) else ""),
        "",
        "| Scenario | Result | Time | Failure |",
        "|---|---|---|---|",
    ]
    for name, v in ran.items():
        mark = {"passed": "pass", "not run": "not run"}.get(v["outcome"], "**FAIL**")
        reason = v["reason"].replace("|", "\\|")
        lines.append(f"| {v['scenario']} (`{name}`) | {mark} | {v['seconds']:.0f}s | {reason} |")
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
async def convo(request, business_id):
    from evals.harness import open_conversation

    started = time.perf_counter()
    async with open_conversation(business_id) as conversation:
        yield conversation
    conversation.save_transcript(RESULTS_DIR / "transcripts" / f"{request.node.name}.txt", time.perf_counter() - started)
