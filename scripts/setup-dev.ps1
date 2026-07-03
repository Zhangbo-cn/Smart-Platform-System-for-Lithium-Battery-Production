# 平台开发环境一键安装
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }

Write-Host "==> pip install platform-contracts + harness-core"
& $Py -m pip install -e "$Root\packages\platform-contracts" -e "$Root\packages\harness-core"

$RcaPath = Join-Path $Root "services\a2a_server\rca-agent\pyproject.toml"
if (Test-Path $RcaPath) {
    Write-Host "==> pip install rca-agent (requires platform-contracts)"
    & $Py -m pip install -e "$Root\services\a2a_server\rca-agent"
}

Write-Host "Done. Start services per docs/REPO_LAYOUT.md"
