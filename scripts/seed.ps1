# Re-seeds the demo business + fake availability (safe to re-run any time;
# regenerates the next-14-days appointment slots relative to today).
$RootDir = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $RootDir "backend")
try {
    & ".venv\Scripts\python.exe" -m app.database.seed
} finally {
    Pop-Location
}
