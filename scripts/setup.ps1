# Voice Agent V0 -- one-shot local setup (Windows / PowerShell).
# Safe to re-run: never overwrites an existing .env.

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot

Write-Host "=== Voice Agent V0 setup ===" -ForegroundColor Cyan

function Get-PythonCmd {
    $tries = @(
        @{ Cmd = "py"; Args = @("-3.13") },
        @{ Cmd = "py"; Args = @("-3.12") },
        @{ Cmd = "py"; Args = @("-3.11") },
        @{ Cmd = "python3.13"; Args = @() },
        @{ Cmd = "python3.12"; Args = @() },
        @{ Cmd = "python3.11"; Args = @() },
        @{ Cmd = "python"; Args = @() },
        @{ Cmd = "python3"; Args = @() }
    )
    foreach ($t in $tries) {
        try {
            $checkArgs = $t.Args + @("--version")
            $out = & $t.Cmd @checkArgs 2>&1
            if ($LASTEXITCODE -eq 0 -and $out -match "Python 3\.(\d+)") {
                if ([int]$Matches[1] -ge 11) { return $t }
            }
        } catch {}
    }
    return $null
}

# --- 1. Python ---
$py = Get-PythonCmd
if (-not $py) {
    Write-Host "ERROR: Python 3.11+ was not found on PATH." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ (check 'Add python.exe to PATH') and re-run this script."
    exit 1
}
Write-Host "Using Python: $($py.Cmd) $($py.Args -join ' ')"

# --- 2. Backend venv + dependencies ---
Push-Location (Join-Path $RootDir "backend")
try {
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating backend virtual environment (backend\.venv)..."
        $venvArgs = $py.Args + @("-m", "venv", ".venv")
        & $py.Cmd @venvArgs
    } else {
        Write-Host "backend\.venv already exists, reusing it."
    }

    $venvPython = ".venv\Scripts\python.exe"
    Write-Host "Installing backend dependencies (this can take a minute)..."
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -e ".[dev]"

    New-Item -ItemType Directory -Force -Path "data" | Out-Null
    New-Item -ItemType Directory -Force -Path "logs" | Out-Null
} finally {
    Pop-Location
}

# --- 3. .env files (never overwrite existing ones) ---
$envPath = Join-Path $RootDir ".env"
if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $RootDir ".env.example") $envPath
    Write-Host "Created .env from .env.example -- fill in real credentials before starting a call." -ForegroundColor Yellow
} else {
    Write-Host ".env already exists, leaving it untouched."
}

$frontendEnvPath = Join-Path $RootDir "frontend\.env.local"
if (-not (Test-Path $frontendEnvPath)) {
    Copy-Item (Join-Path $RootDir "frontend\.env.local.example") $frontendEnvPath
    Write-Host "Created frontend\.env.local from frontend\.env.local.example."
} else {
    Write-Host "frontend\.env.local already exists, leaving it untouched."
}

# --- 4. Frontend dependencies ---
Push-Location (Join-Path $RootDir "frontend")
try {
    Write-Host "Installing frontend dependencies (npm install)..."
    npm install
} finally {
    Pop-Location
}

# --- 5. Initialize + seed database ---
Push-Location (Join-Path $RootDir "backend")
try {
    Write-Host "Initializing database and seeding demo data (Sharma Dental Care)..."
    & ".venv\Scripts\python.exe" -m app.database.seed

    Write-Host "Running backend test suite..."
    & ".venv\Scripts\python.exe" -m pytest -q
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Edit .env with your real LiveKit / Sarvam / OpenAI credentials."
Write-Host "  2. In three separate terminals, run:"
Write-Host "       scripts\dev-backend.ps1"
Write-Host "       scripts\dev-agent.ps1"
Write-Host "       scripts\dev-frontend.ps1"
Write-Host "  3. Open http://localhost:3000"
