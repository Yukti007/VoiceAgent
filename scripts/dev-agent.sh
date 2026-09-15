#!/usr/bin/env bash
# Starts the LiveKit Agents worker (Aisha): STT -> LLM -> TTS + tools.
# Requires real LIVEKIT_*/SARVAM_API_KEY/OPENAI_API_KEY values in .env.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR/backend"
.venv/bin/python -m app.agent.agent dev
