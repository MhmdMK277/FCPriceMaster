Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

# ---------------------------------------------------------------------------
# Tool resolution — fail fast with a clear message.
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
    Write-Error "uv not found. Install from https://docs.astral.sh/uv/getting-started/installation/ then re-run setup."
    exit 1
}
if (-not $pnpmCmd) {
    Write-Error "pnpm not found. Run 'npm install -g pnpm' then re-run setup."
    exit 1
}

Write-Host "==> uv:   $uvExe" -ForegroundColor DarkGray
Write-Host "==> pnpm: $pnpmCmd" -ForegroundColor DarkGray

Write-Host "==> Installing Python backend dependencies..." -ForegroundColor Cyan
Push-Location "$root\backend"
& $uvExe sync
Pop-Location

Write-Host "==> Installing frontend dependencies..." -ForegroundColor Cyan
Push-Location "$root\frontend"
& $pnpmCmd install
Pop-Location

Write-Host "==> Installing Playwright Chromium browser..." -ForegroundColor Cyan
Push-Location "$root\backend"
& $uvExe run playwright install chromium
Pop-Location

Write-Host "==> Setup complete." -ForegroundColor Green
