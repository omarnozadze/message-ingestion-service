$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "start-whatsapp.ps1") `
    -FastApiUrl "http://127.0.0.1:8011" `
    -SelfTest `
    -SelfTestSenderId "+995555000999"
exit $LASTEXITCODE
