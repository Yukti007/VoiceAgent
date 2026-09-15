#!/usr/bin/env bash
# Re-seeds the demo business + fake availability (safe to re-run any time;
# regenerates the next-14-days appointment slots relative to today).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR/backend"
.venv/bin/python -m app.database.seed
