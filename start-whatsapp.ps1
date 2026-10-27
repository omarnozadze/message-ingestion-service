[CmdletBinding()]
param(
    [string]$FastApiUrl = "http://127.0.0.1:8011",
    [switch]$SelfTest,
    [string]$SelfTestSenderId = "+995555000999"
)

$ErrorActionPreference = "Stop"
$bridge = Join-Path $PSScriptRoot "whatsapp-bridge"
Set-Location $bridge

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Node.js 20+ is required." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path "node_modules")) {
    Write-Host "node_modules is missing; running npm ci..." -ForegroundColor Yellow
    npm ci
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$env:FASTAPI_URL = $FastApiUrl
$env:FORWARD_TIMEOUT_MS = "30000"
$env:STATUS_POLL_INTERVAL_MS = "1000"
$env:STATUS_POLL_MAX_MS = "1500000"

if ($SelfTest) {
    $env:ALLOW_SELF_MESSAGES = "true"
    $env:SELF_TEST_SENDER_ID = $SelfTestSenderId
    Write-Host "Starting WhatsApp bridge in SELF-TEST mode ($SelfTestSenderId)..." -ForegroundColor Yellow
}
else {
    Remove-Item Env:ALLOW_SELF_MESSAGES -ErrorAction SilentlyContinue
    Remove-Item Env:SELF_TEST_SENDER_ID -ErrorAction SilentlyContinue
    Write-Host "Starting WhatsApp bridge in normal mode..." -ForegroundColor Green
}

npm start
