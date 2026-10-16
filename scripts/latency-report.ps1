# Summarises per-stage latency (median / p90) from real calls, as a Markdown table.
# Reads backend/logs/latency.jsonl, written by the agent worker at the end of each call.
$RootDir = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $RootDir "backend")
try {
    & ".venv\Scripts\python.exe" -m app.agent.latency @args
} finally {
    Pop-Location
}
