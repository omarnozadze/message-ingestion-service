<#
.SYNOPSIS
    Prepares the project environment (Python deps, DB migrations, WhatsApp bridge deps).

.DESCRIPTION
    Safe to run repeatedly. Never overwrites an existing .env or the WhatsApp auth folder.

      .\install.ps1                  # everything
      .\install.ps1 -SkipBridge      # Python only (used by start-api.ps1)
      .\install.ps1 -SkipMigrations  # do not run Alembic
      .\install.ps1 -RunTests        # also run the unit tests
#>
[CmdletBinding()]
param(
    [switch]$SkipBridge,
    [switch]$SkipMigrations,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not installed. Install it with:  winget install astral-sh.uv" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".env")) {
    Step "Creating .env from .env.example (fill in 1C / OpenAI credentials afterwards)"
    Copy-Item ".env.example" ".env"
}

Step "Installing Python dependencies (uv sync --dev)"
uv sync --dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipMigrations) {
    Step "Applying database migrations (alembic upgrade head)"
    uv run alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Migration failed. Is PostgreSQL running and DATABASE_URL correct in .env?" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if (-not $SkipBridge) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Host "Node.js 20+ is required for the WhatsApp bridge:  winget install OpenJS.NodeJS.LTS" -ForegroundColor Red
        exit 1
    }
    Step "Installing WhatsApp bridge dependencies (npm ci)"
    Push-Location "whatsapp-bridge"
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        node --check ".\src\index.js"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally { Pop-Location }
}

if ($RunTests) {
    Step "Running unit tests"
    uv run pytest -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host "  API:       .\start-api.ps1        (http://127.0.0.1:8011/docs)"
Write-Host "  WhatsApp:  .\start-whatsapp.ps1"
