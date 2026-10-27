[CmdletBinding()]
param(
    [string]$FastApiUrl = "http://127.0.0.1:8011",
    [string]$SenderId = "+995555000999",
    [string]$AgentCode = "202"
)

$ErrorActionPreference = "Stop"
$baseUrl = $FastApiUrl.TrimEnd("/")

Write-Host "Checking FastAPI health..." -ForegroundColor Cyan
$health = Invoke-RestMethod -Uri "$baseUrl/health" -Method Get -TimeoutSec 15
Write-Host "Health: $($health.status)" -ForegroundColor Green

$body = @{
    source = "whatsapp"
    external_message_id = "preflight-$AgentCode-$(Get-Date -Format yyyyMMddHHmmss)"
    sender_id = $SenderId
    sender_name = "SELF TEST"
    text = "$AgentCode test"
} | ConvertTo-Json -Compress

Write-Host "Checking sender/agent preflight for $AgentCode..." -ForegroundColor Cyan
$result = Invoke-RestMethod `
    -Uri "$baseUrl/api/v1/incoming-message/preflight" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
    -TimeoutSec 15

if ($result.authorized -ne $true -or "$($result.agent_code)" -ne $AgentCode) {
    throw "Preflight failed: authorized=$($result.authorized), agent_code=$($result.agent_code)"
}

Write-Host "PREFLIGHT OK: $($result.agent_description) via $($result.resolution_source)" -ForegroundColor Green
