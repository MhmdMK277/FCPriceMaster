Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

Write-Host "==> Installing Python backend dependencies..." -ForegroundColor Cyan
Push-Location "$root\backend"
uv sync
Pop-Location

Write-Host "==> Installing frontend dependencies..." -ForegroundColor Cyan
Push-Location "$root\frontend"
pnpm install
Pop-Location

Write-Host "==> Setup complete." -ForegroundColor Green
