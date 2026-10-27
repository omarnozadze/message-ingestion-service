[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8011,
    [ValidateSet("critical", "error", "warning", "info", "debug", "trace")]
    [string]$LogLevel = "info"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not installed or is not in PATH." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".venv")) {
    Write-Host ".venv is missing; rebuilding it first..." -ForegroundColor Yellow
    & ".\install.ps1" -SkipBridge
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$env:API_HOST = $HostAddress
$env:API_PORT = "$Port"
$env:LOG_LEVEL = $LogLevel

Write-Host "Starting FastAPI at http://${HostAddress}:$Port" -ForegroundColor Green
Write-Host "Use this launcher on Windows; it selects psycopg-compatible SelectorEventLoop." -ForegroundColor DarkGray
uv run python run_api.py
