# 将姊妹仓 Battery_Agent_DS 联接为 services/a2a_server/rca-agent（Windows 目录联接）
# 用法：在 Battery_Agent_DS_Aug 根目录执行  .\scripts\link-rca.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LinkPath = Join-Path $Root "services\a2a_server\rca-agent"
$TargetPath = Resolve-Path (Join-Path $Root "..\Battery_Agent_DS") -ErrorAction SilentlyContinue

if (-not $TargetPath) {
    $TargetPath = "E:\new_work\my_self\project\Battery_Agent_DS"
    Write-Warning "未找到 ../Battery_Agent_DS，使用默认路径: $TargetPath"
}

if (-not (Test-Path $TargetPath)) {
    Write-Error "RCA 目标路径不存在: $TargetPath"
}

$parentDir = Split-Path $LinkPath -Parent
if (-not (Test-Path $parentDir)) {
    New-Item -ItemType Directory -Path $parentDir | Out-Null
}

if (Test-Path $LinkPath) {
  $item = Get-Item $LinkPath -Force
  if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Write-Host "已存在联接: $LinkPath -> $($item.Target)"
    exit 0
  }
  $children = Get-ChildItem $LinkPath -Force -ErrorAction SilentlyContinue
  if ($children.Count -le 1) {
    Remove-Item $LinkPath -Recurse -Force
  } else {
    Write-Error "services/a2a_server/rca-agent 已存在且不是联接目录，请手动处理后再运行"
  }
}

New-Item -ItemType Junction -Path $LinkPath -Target $TargetPath | Out-Null
Write-Host "OK: $LinkPath -> $TargetPath"
Write-Host "启动 RCA: cd services\a2a_server\rca-agent && uvicorn api.main:app --port 8003"
