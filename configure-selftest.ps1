[CmdletBinding()]
param(
    [string]$SenderId = "+995555000999",
    [string[]]$AgentCodes = @("103", "110", "111", "112", "115", "117", "202", "402", "403", "504")
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is required." -ForegroundColor Red
    exit 1
}

uv run python scripts/configure_sender.py `
    --env ".env" `
    --sender $SenderId `
    --agents $AgentCodes `
    --processing-timeout 1200
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Self-test sender configuration updated." -ForegroundColor Green
Write-Host "Restart FastAPI so Settings reloads the changed .env file." -ForegroundColor Yellow
