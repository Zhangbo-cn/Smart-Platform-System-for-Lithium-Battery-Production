# 停止本地开发进程（uvicorn / 项目端口）
$ports = 8003, 8004, 8010, 8020, 8021, 8001, 8002, 8101, 8102, 8103, 8104, 8105
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
