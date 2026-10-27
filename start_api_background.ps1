$ErrorActionPreference = "Continue"

$ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$PythonExe   = Join-Path $ProjectRoot ".venv-approved\Scripts\python.exe"
$RunApi      = Join-Path $ProjectRoot "run_api.py"
$LogsDir     = Join-Path $ProjectRoot "logs"

New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

# Give Windows/PostgreSQL/network services time to settle after boot.
Start-Sleep -Seconds 20
Set-Location $ProjectRoot

$stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
$logFile = Join-Path $LogsDir "api-$stamp.log"

# Keep stdout + stderr together because Uvicorn writes normal INFO messages to stderr.
& $PythonExe $RunApi *>> $logFile
exit $LASTEXITCODE
