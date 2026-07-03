# 启动基础设施（redis + postgres + neo4j）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
docker compose -f deploy/docker-compose.platform.yml up -d redis postgres neo4j
Write-Host "Infra up. MCP: docker compose -f deploy/docker-compose.platform.yml --profile mcp up -d"
