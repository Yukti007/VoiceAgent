# Runs the backend test suite.
$RootDir = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $RootDir "backend")
try {
    & ".venv\Scripts\python.exe" -m pytest -q
} finally {
    Pop-Location
}
