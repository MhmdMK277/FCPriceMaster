Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

# ---------------------------------------------------------------------------
# Tool resolution — fail fast with a clear message rather than a cryptic
# "not recognized" error propagated from a child process.
# ---------------------------------------------------------------------------
function Resolve-Tool {
    param([string]$Name, [string[]]$FallbackPaths)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in $FallbackPaths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$uvExe = Resolve-Tool 'uv' @(
    "$env:USERPROFILE\.local\bin\uv.exe",
    "$env:LOCALAPPDATA\uv\bin\uv.exe",
    "C:\Users\khoba\.local\bin\uv.exe"
)
$pnpmCmd = Resolve-Tool 'pnpm' @(
    "$env:APPDATA\npm\pnpm.cmd",
    "$env:LOCALAPPDATA\pnpm\pnpm.cmd",
    "$env:USERPROFILE\AppData\Roaming\npm\pnpm.cmd"
)

if (-not $uvExe) {
    Write-Error "uv not found. Tried PATH, $env:USERPROFILE\.local\bin\uv.exe, $env:LOCALAPPDATA\uv\bin\uv.exe"
    exit 1
}
if (-not $pnpmCmd) {
    Write-Error "pnpm not found. Tried PATH, $env:APPDATA\npm\pnpm.cmd, $env:LOCALAPPDATA\pnpm\pnpm.cmd"
    exit 1
}

Write-Host "==> uv:   $uvExe" -ForegroundColor DarkGray
Write-Host "==> pnpm: $pnpmCmd" -ForegroundColor DarkGray
Write-Host "==> Starting FCPriceMaster dev environment..." -ForegroundColor Cyan

# Ensure data/logs dir exists
New-Item -ItemType Directory -Force -Path "$root\data\logs" | Out-Null

# Per-worker toggles: set to "false" to skip that worker.
$enableDiscord  = ($env:ENABLE_DISCORD_INGEST  -ne "false")
$enableTwitter  = ($env:ENABLE_TWITTER_INGEST  -ne "false")

# Launch backend scheduler in a separate window, capturing PID for cleanup.
$backendProc = Start-Process -FilePath "powershell" `
    -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; & '$uvExe' run python -m src.workers.scheduler" `
    -PassThru

Write-Host "==> Backend scheduler PID: $($backendProc.Id)" -ForegroundColor Yellow

# Launch Discord ingest worker (separate long-running WebSocket process)
$discordProc = $null
if ($enableDiscord) {
    $discordProc = Start-Process -FilePath "powershell" `
        -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; & '$uvExe' run python -m src.workers.discord_ingest" `
        -PassThru
    Write-Host "==> Discord ingest PID: $($discordProc.Id)" -ForegroundColor Yellow
} else {
    Write-Host "==> Discord ingest skipped (ENABLE_DISCORD_INGEST=false)" -ForegroundColor DarkGray
}

# Launch Twitter ingest worker (separate long-running Playwright process)
$twitterProc = $null
if ($enableTwitter) {
    $twitterProc = Start-Process -FilePath "powershell" `
        -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; & '$uvExe' run python -m src.workers.twitter_ingest" `
        -PassThru
    Write-Host "==> Twitter ingest PID: $($twitterProc.Id)" -ForegroundColor Yellow
} else {
    Write-Host "==> Twitter ingest skipped (ENABLE_TWITTER_INGEST=false)" -ForegroundColor DarkGray
}

try {
    # Launch Electron + Vite dev server (blocks until Electron window closes).
    # dev.ps1 already spawned the workers above — tell Electron NOT to spawn its own.
    $env:AUTO_START_BACKEND = "false"
    Push-Location "$root\frontend"
    & $pnpmCmd dev:electron
    Pop-Location
} finally {
    Write-Host "==> Shutting down process trees..." -ForegroundColor Yellow
    try {
        & taskkill /F /T /PID $backendProc.Id 2>$null
        Write-Host "==> Backend stopped." -ForegroundColor Green
    } catch {
        Write-Host "==> Backend already stopped." -ForegroundColor DarkGray
    }
    if ($discordProc) {
        try {
            & taskkill /F /T /PID $discordProc.Id 2>$null
            Write-Host "==> Discord ingest stopped." -ForegroundColor Green
        } catch {
            Write-Host "==> Discord ingest already stopped." -ForegroundColor DarkGray
        }
    }
    if ($twitterProc) {
        try {
            & taskkill /F /T /PID $twitterProc.Id 2>$null
            Write-Host "==> Twitter ingest stopped." -ForegroundColor Green
        } catch {
            Write-Host "==> Twitter ingest already stopped." -ForegroundColor DarkGray
        }
    }
}
