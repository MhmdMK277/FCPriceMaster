Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

Write-Host "==> Starting FCPriceMaster dev environment..." -ForegroundColor Cyan

# Ensure data dir exists for the DB
New-Item -ItemType Directory -Force -Path "$root\data" | Out-Null

# Launch backend scheduler in a new window
$backendJob = Start-Process -FilePath "powershell" `
    -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; uv run python -m backend.workers.scheduler" `
    -PassThru

Write-Host "==> Backend PID: $($backendJob.Id)" -ForegroundColor Yellow

# Launch Electron + Vite dev server
Push-Location "$root\frontend"
pnpm dev:electron
Pop-Location
