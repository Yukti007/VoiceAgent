# Starts the LiveKit Agents worker (Aisha): STT -> LLM -> TTS + tools.
# Requires real LIVEKIT_*/SARVAM_API_KEY/OPENAI_API_KEY values in .env.
$RootDir = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $RootDir "backend")
try {
    & ".venv\Scripts\python.exe" -m app.agent.agent dev
} finally {
    Pop-Location
}
