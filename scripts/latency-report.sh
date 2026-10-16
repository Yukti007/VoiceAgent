#!/usr/bin/env bash
# Summarises per-stage latency (median / p90) from real calls, as a Markdown table.
# Reads backend/logs/latency.jsonl, written by the agent worker at the end of each call.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR/backend"
.venv/bin/python -m app.agent.latency "$@"
