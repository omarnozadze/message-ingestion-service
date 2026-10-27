$ErrorActionPreference = "Continue"

$ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$BridgeRoot  = Join-Path $ProjectRoot "whatsapp-bridge"
$LogsDir     = Join-Path $ProjectRoot "logs"
$NpmExe      = 'C:\Program Files\nodejs\npm.cmd'

New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

# Start after FastAPI has had time to come up.
Start-Sleep -Seconds 45

$env:FASTAPI_URL = "http://127.0.0.1:8011"
$env:ALLOW_SELF_MESSAGES = "true"
$env:SELF_TEST_SENDER_ID = "+995555000999"

Set-Location $BridgeRoot

$stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$logFile = Join-Path $LogsDir "whatsapp-bridge-$stamp.log"

& $NpmExe start *>> $logFile
exit $LASTEXITCODE
