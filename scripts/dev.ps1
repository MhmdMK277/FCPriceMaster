Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

Write-Host "==> Starting FCPriceMaster dev environment..." -ForegroundColor Cyan

# Ensure data/logs dir exists
New-Item -ItemType Directory -Force -Path "$root\data\logs" | Out-Null

# Launch backend scheduler in a separate window, capturing PID for cleanup.
# The scheduler spawns Playwright/Chromium as grandchildren, so we use
# 'taskkill /F /T' on shutdown to kill the entire process tree recursively.
$backendProc = Start-Process -FilePath "powershell" `
    -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; uv run python -m src.workers.scheduler" `
    -PassThru

Write-Host "==> Backend PID: $($backendProc.Id)" -ForegroundColor Yellow

try {
    # Launch Electron + Vite dev server (blocks until Electron window closes)
    Push-Location "$root\frontend"
    pnpm dev:electron
    Pop-Location
} finally {
    # Use taskkill /F /T to recursively kill the entire process tree rooted at
    # the backend PID. This catches Python, the PowerShell wrapper, and any
    # Chromium.exe grandchildren spawned by Playwright.
    Write-Host "==> Shutting down backend process tree (PID $($backendProc.Id))..." -ForegroundColor Yellow
    try {
        & taskkill /F /T /PID $backendProc.Id 2>$null
        Write-Host "==> Backend stopped." -ForegroundColor Green
    } catch {
        Write-Host "==> Backend already stopped." -ForegroundColor DarkGray
    }
}
