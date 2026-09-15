#!/usr/bin/env bash
# Voice Agent V0 -- one-shot local setup (macOS / Linux).
# Safe to re-run: never overwrites an existing .env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Voice Agent V0 setup ==="

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)"
    major="$(echo "$version" | cut -d. -f1)"
    minor="$(echo "$version" | cut -d. -f2)"
    if [ "$major" = "3" ] && [ "$minor" -ge 11 ] 2>/dev/null; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "ERROR: Python 3.11+ was not found on PATH."
  echo "Install it from https://www.python.org/downloads/ and re-run this script."
  exit 1
fi
echo "Using Python: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

cd "$ROOT_DIR/backend"
if [ ! -d ".venv" ]; then
  echo "Creating backend virtual environment (backend/.venv)..."
  "$PYTHON_BIN" -m venv .venv
else
  echo "backend/.venv already exists, reusing it."
fi

VENV_PY=".venv/bin/python"
echo "Installing backend dependencies (this can take a minute)..."
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -e ".[dev]"
mkdir -p data logs

cd "$ROOT_DIR"
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example -- fill in real credentials before starting a call."
else
  echo ".env already exists, leaving it untouched."
fi

if [ ! -f "frontend/.env.local" ]; then
  cp frontend/.env.local.example frontend/.env.local
  echo "Created frontend/.env.local from frontend/.env.local.example."
else
  echo "frontend/.env.local already exists, leaving it untouched."
fi

echo "Installing frontend dependencies (npm install)..."
(cd frontend && npm install)

cd "$ROOT_DIR/backend"
echo "Initializing database and seeding demo data (Sharma Dental Care)..."
"$VENV_PY" -m app.database.seed

echo "Running backend test suite..."
"$VENV_PY" -m pytest -q

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. Edit .env with your real LiveKit / Sarvam / OpenAI credentials."
echo "  2. In three separate terminals, run:"
echo "       ./scripts/dev-backend.sh"
echo "       ./scripts/dev-agent.sh"
echo "       ./scripts/dev-frontend.sh"
echo "  3. Open http://localhost:3000"
