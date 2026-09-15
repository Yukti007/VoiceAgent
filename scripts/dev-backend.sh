#!/usr/bin/env bash
# Starts the FastAPI HTTP API (token minting, calls/appointments/extractions).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR/backend"
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
