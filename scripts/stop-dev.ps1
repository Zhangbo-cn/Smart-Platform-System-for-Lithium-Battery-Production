# 停止本地开发进程（uvicorn / 项目端口）
$ports = @(
    8001,  # triage-agent
    8002,  # trace-worker
    8003,  # rca-agent
    8004,  # reporter-agent
    8005,  # patrol-agent
    8010,  # client-gateway
    8011,  # planner-agent
    8020,  # orchestrator
    8021,  # capability-registry
    8099,  # safety-agent
    8101,  # mcp-mes
    8102,  # mcp-scada
    8103,  # mcp-erp
    8104,  # mcp-lims
    8105,  # mcp-qms
    8106,  # mcp-knowledge
    8107,  # mcp-eam
    8108,  # mcp-wms
    8110,  # mcp-plc
    8201,  # quality-prediction
    8202,  # process-optimization
    8203,  # equipment-health
    8204   # wms-supply
)
$killed = @()

foreach ($port in $ports) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conn) {
        $pid = $c.OwningProcess
        if ($pid -and $killed -notcontains $pid) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            $killed += $pid
            Write-Host "Stopped PID $pid (port $port)"
        }
    }
}

if ($killed.Count -eq 0) {
    Write-Host "No listeners on project ports."
}

# 可选：停 docker compose
if ($args -contains "-docker") {
    Push-Location (Split-Path $PSScriptRoot -Parent)
    docker compose -f deploy/docker-compose.platform.yml --profile mcp down 2>$null
    Pop-Location
    Write-Host "Docker compose down (if was running)."
}
