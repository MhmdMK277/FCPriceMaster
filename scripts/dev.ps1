Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

Write-Host "==> Starting FCPriceMaster dev environment..." -ForegroundColor Cyan

# Ensure data dir exists for the DB
New-Item -ItemType Directory -Force -Path "$root\data" | Out-Null

# Launch backend scheduler as a child process, capturing PID for cleanup
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
    # Kill backend and all its children when Electron exits
    Write-Host "==> Shutting down backend (PID $($backendProc.Id))..." -ForegroundColor Yellow
    try {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($backendProc.Id)" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue
        Write-Host "==> Backend stopped." -ForegroundColor Green
    } catch {
        Write-Host "==> Backend already stopped." -ForegroundColor DarkGray
    }
}
