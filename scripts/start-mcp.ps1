# 启动平台 MCP Server（8101-8105）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$McpRoot = Join-Path $Root "services\mcp"
$Py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }

$mods = @(
    "mes_server.mes_server",
    "scada_server.scada_server",
    "erp_server.erp_server",
    "lims_server.lims_server",
    "qms_server.qms_server"
)
foreach ($mod in $mods) {
    Start-Process -FilePath $Py -ArgumentList "-m", $mod -WorkingDirectory $McpRoot -WindowStyle Minimized
}
Write-Host "MCP started from $McpRoot (ports 8101-8105)"
