$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Node.js is not installed. Install Node.js 20+ first." -ForegroundColor Red
    Write-Host 'Example: winget install OpenJS.NodeJS.LTS'
    exit 1
}

$nodeMajor = [int]((node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 20) {
    Write-Host "Node.js 20+ is required. Current: $(node --version)" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path .\node_modules)) {
    Write-Host "Installing WhatsApp bridge dependencies..." -ForegroundColor Cyan
    npm install
}

Write-Host "Starting WhatsApp bridge..." -ForegroundColor Green
Write-Host "On the first run a QR code will appear." -ForegroundColor Green
Write-Host "WhatsApp -> Settings -> Linked devices -> Link a device" -ForegroundColor Green
npm start
