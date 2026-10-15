"""Per-turn latency, recorded from real calls and summarised as median/p90.

The numbers come from LiveKit's own per-turn `ChatMessage.metrics` (user
turns: transcription and end-of-turn delay; agent turns: LLM time to first
token, TTS time to first audio, and end-to-end "caller stopped speaking ->
agent started responding"), plus the wall time of every tool call.

Rows are buffered in memory during the call and appended to a JSON-lines file
when it ends, so the audio event loop never waits on disk.

    python -m app.agent.latency               # summarise logs/latency.jsonl
    python -m app.agent.latency path.jsonl    # or another file
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

from app.config import LOGS_DIR

LATENCY_LOG = LOGS_DIR / "latency.jsonl"

# ChatMessage.metrics field -> stage name in the log and report.
TURN_STAGES = {
    "transcription_delay": "stt_final",
    "end_of_turn_delay": "end_of_turn",
    "llm_node_ttft": "llm_ttft",
    "tts_node_ttfb": "tts_ttfb",
    "e2e_latency": "e2e",
}
STAGE_ORDER = ["stt_final", "end_of_turn", "llm_ttft", "tts_ttfb", "e2e"]
STAGE_LABELS = {
    "stt_final": "STT final transcript after end of speech",
    "end_of_turn": "End-of-turn decision after end of speech",
    "llm_ttft": "LLM time to first token",
    "tts_ttfb": "TTS time to first audio",
    "e2e": "End to end (caller stops -> agent starts)",
}


class LatencyRecorder:
    def __init__(self, call_id: str, path: Path = LATENCY_LOG) -> None:
        self.call_id = call_id
        self._path = path
        self._rows: list[dict] = []

    @property
    def rows(self) -> list[dict]:
        return list(self._rows)

    def record_turn(self, metrics: Mapping[str, float] | None) -> list[dict]:
        """Record whichever stage timings this turn's metrics carry."""
        added = []
        for field, stage in TURN_STAGES.items():
            value = (metrics or {}).get(field)
            if isinstance(value, (int, float)) and value >= 0:
                added.append(self._add(stage, float(value)))
        return added

    def record_tool(self, name: str, seconds: float) -> dict:
        return self._add(f"tool:{name}", seconds)

    def _add(self, stage: str, seconds: float) -> dict:
        row = {"call_id": self.call_id, "ts": round(time.time(), 3), "stage": stage, "seconds": round(seconds, 4)}
        self._rows.append(row)
        return row

    def flush(self) -> None:
        if not self._rows:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            for row in self._rows:
                f.write(json.dumps(row) + "\n")
        self._rows.clear()

    async def aflush(self) -> None:
        await asyncio.to_thread(self.flush)


def load_rows(path: Path = LATENCY_LOG) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _p90(values: list[float]) -> float:
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=10, method="inclusive")[8]


def summarize(rows: Iterable[dict]) -> list[dict]:
    """One summary per stage: samples, median, p90, max (seconds)."""
    by_stage: dict[str, list[float]] = {}
    calls: dict[str, set[str]] = {}
    for row in rows:
        by_stage.setdefault(row["stage"], []).append(float(row["seconds"]))
        calls.setdefault(row["stage"], set()).add(row["call_id"])
    ordered = [s for s in STAGE_ORDER if s in by_stage] + sorted(s for s in by_stage if s not in STAGE_ORDER)
    return [
        {
            "stage": stage,
            "label": STAGE_LABELS.get(stage, stage.replace("tool:", "Tool: ")),
            "samples": len(by_stage[stage]),
            "calls": len(calls[stage]),
            "median": statistics.median(by_stage[stage]),
            "p90": _p90(by_stage[stage]),
            "max": max(by_stage[stage]),
        }
        for stage in ordered
    ]


def to_markdown(summary: list[dict]) -> str:
    lines = [
        "| Stage | Samples (calls) | Median | p90 | Max |",
        "|---|---|---|---|---|",
    ]
    for s in summary:
        lines.append(
            f"| {s['label']} | {s['samples']} ({s['calls']}) | {s['median'] * 1000:.0f} ms "
            f"| {s['p90'] * 1000:.0f} ms | {s['max'] * 1000:.0f} ms |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else LATENCY_LOG
    summary = summarize(load_rows(source))
    if not summary:
        print(f"No latency rows in {source}. Make a few calls with the agent worker running first.")
        sys.exit(1)
    print(to_markdown(summary))
