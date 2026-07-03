# E2E close_loop：Gateway → Orchestrator → RCA → Reporter
# 前置：orchestrator(8020)、report-reporter-agent(8004)、rca(8003) 已启动；RCA/Reporter 可无 LLM（走 fallback）

$ErrorActionPreference = "Stop"
$Base = "http://127.0.0.1:8010"
$Orch = "http://127.0.0.1:8020"
$Headers = @{
    "Authorization" = "Bearer dev-token"
    "X-Internal-Service-Key" = "dev-router-key"
    "Content-Type" = "application/json"
}

$sessionId = "e2e-close-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
Write-Host "Session: $sessionId"

$body = @{
    session_id = $sessionId
    message = "质量闭环测试"
    playbook = "close_loop"
    batch_id = "B202406001"
    factory_id = "FD-01"
    hitl_approved = $true
    confirm_rca = $true
} | ConvertTo-Json

Write-Host "POST orchestrator dispatch..."
$resp = Invoke-RestMethod -Uri "$Orch/v1/dispatch" -Method POST -Headers $Headers -Body $body
Write-Host "Dispatch status:" $resp.task_status
Write-Host "RCA root_cause:" $resp.rca_result.root_cause
Write-Host "8D capa_id:" $resp.report_8d_result.capa_id
Write-Host "8D generation_mode:" $resp.report_8d_result.generation_mode

if (-not $resp.report_8d_result.report_md) {
    Write-Error "E2E failed: no report_md"
}
Write-Host "E2E close_loop OK"
