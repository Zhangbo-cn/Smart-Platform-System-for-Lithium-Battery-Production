# 启动平台 MCP Server（8101-8110）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$McpRoot = Join-Path $Root "services\mcp"
$Py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }

$mods = @(
    "mes_server.mes_server",       # 8101
    "scada_server.scada_server",   # 8102
    "erp_server.erp_server",       # 8103
    "lims_server.lims_server",     # 8104
    "qms_server.qms_server",       # 8105
    "knowledge_server.app",         # 8106
    "eam_server.eam_server",       # 8107
    "wms_server.wms_server",       # 8108
    "plc_server.plc_server"        # 8110
)
foreach ($mod in $mods) {
    Start-Process -FilePath $Py -ArgumentList "-m", $mod -WorkingDirectory $McpRoot -WindowStyle Minimized
}
Write-Host "MCP started from $McpRoot (ports 8101-8110, 9 servers)"
