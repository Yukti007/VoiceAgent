#!/usr/bin/env bash
# Runs the backend test suite.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR/backend"
.venv/bin/python -m pytest -q
