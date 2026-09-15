# Starts the FastAPI HTTP API (token minting, calls/appointments/extractions).
$RootDir = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $RootDir "backend")
try {
    & ".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
} finally {
    Pop-Location
}
