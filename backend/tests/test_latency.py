import pytest

from app.agent.latency import LatencyRecorder, load_rows, summarize, to_markdown


def test_turn_metrics_become_stage_rows(tmp_path):
    rec = LatencyRecorder("call-1", path=tmp_path / "latency.jsonl")
    user_rows = rec.record_turn({"transcription_delay": 0.21, "end_of_turn_delay": 0.45, "started_speaking_at": 9.0})
    agent_rows = rec.record_turn({"llm_node_ttft": 0.52, "tts_node_ttfb": 0.19, "e2e_latency": 1.3})
    assert [r["stage"] for r in user_rows] == ["stt_final", "end_of_turn"]
    assert [r["stage"] for r in agent_rows] == ["llm_ttft", "tts_ttfb", "e2e"]
    assert rec.record_turn(None) == [] and rec.record_turn({}) == []


def test_flush_appends_and_summary_reports_median_and_p90(tmp_path):
    path = tmp_path / "latency.jsonl"
    for call in ("a", "b"):
        rec = LatencyRecorder(call, path=path)
        for value in (0.1, 0.2, 0.3, 0.4, 0.5):
            rec.record_turn({"e2e_latency": value})
        rec.record_tool("check_availability", 0.02)
        rec.flush()

    rows = load_rows(path)
    assert len(rows) == 12
    by_stage = {s["stage"]: s for s in summarize(rows)}
    e2e = by_stage["e2e"]
    assert (e2e["samples"], e2e["calls"]) == (10, 2)
    assert e2e["median"] == pytest.approx(0.3)
    assert e2e["p90"] == pytest.approx(0.5)
    assert by_stage["tool:check_availability"]["samples"] == 2

    table = to_markdown(summarize(rows))
    assert "| End to end (caller stops -> agent starts) | 10 (2) | 300 ms | 500 ms | 500 ms |" in table


async def test_tool_calls_are_timed_even_when_they_fail():
    from livekit.agents import ToolError

    from app.agent.agent import _run_db_tool

    rec = LatencyRecorder("call-tools")
    assert await _run_db_tool("get_business_hours", lambda: {"ok": True}, rec) == {"ok": True}

    def broken():
        raise RuntimeError("boom")

    with pytest.raises(ToolError):
        await _run_db_tool("book_appointment", broken, rec)
    assert [r["stage"] for r in rec.rows] == ["tool:get_business_hours", "tool:book_appointment"]
