# 平台开发环境一键安装
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }

Write-Host "==> pip install platform-contracts + harness-core"
& $Py -m pip install -e "$Root\packages\platform-contracts" -e "$Root\packages\harness-core"

# 可选：安装各 Agent 依赖（如有 pyproject.toml）
$AgentDirs = @(
    "services\a2a_server\rca-agent",
    "services\a2a_server\report-agent",
    "services\a2a_server\triage-agent",
    "services\a2a_server\trace_worker"
)
foreach ($dir in $AgentDirs) {
    $Pyproj = Join-Path $Root $dir "pyproject.toml"
    if (Test-Path $Pyproj) {
        Write-Host "==> pip install $dir"
        & $Py -m pip install -e "$Root\$dir"
    }
}

Write-Host "Done. Run 'make test' to verify"
