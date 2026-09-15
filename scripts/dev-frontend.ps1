# Starts the Next.js demo UI.
$RootDir = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $RootDir "frontend")
try {
    npm run dev
} finally {
    Pop-Location
}
