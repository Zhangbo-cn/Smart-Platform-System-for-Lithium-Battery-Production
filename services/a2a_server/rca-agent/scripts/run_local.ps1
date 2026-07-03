# 本地启动 RCA API（MCP 请先运行 ..\..\..\scripts\start-mcp.ps1 或平台 deploy）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }

Set-Location $Root
Write-Host "==> RCA API http://127.0.0.1:8003/docs"
& $Py -m uvicorn api.main:app --host 127.0.0.1 --port 8003 --reload
