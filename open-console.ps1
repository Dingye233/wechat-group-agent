# 群伴控制台 —— 以独立应用窗口（无地址栏、独立任务栏图标）打开后台
# 用法：双击桌面「群伴控制台」，或右键用 PowerShell 运行本脚本
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = 'http://127.0.0.1:8000'

function Test-Service {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $iar = $client.BeginConnect('127.0.0.1', 8000, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(400)
    if ($ok) { $client.EndConnect($iar) }
    $client.Close()
    return [bool]$ok
  } catch {
    return $false
  }
}

# 1) 服务没起就顺手拉起来（复用项目自带的 start.bat，后台静默）
if (-not (Test-Service)) {
  $starter = Join-Path $root 'start.bat'
  if (Test-Path $starter) {
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$starter`"" -WorkingDirectory $root -WindowStyle Hidden
  }
  for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Milliseconds 1000
    if (Test-Service) { break }
  }
}

# 2) 找一个 Chromium 浏览器用 app 模式打开
$candidates = @(
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) {
  Start-Process $url
  return
}

$profileDir = Join-Path $root 'data\console-profile'
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

$browserArgs = @(
  "--app=$url",
  "--user-data-dir=$profileDir",
  "--window-size=1560,1000",
  "--app-window-size=1560,1000",
  "--no-first-run",
  "--no-default-browser-check"
)
Start-Process -FilePath $exe -ArgumentList $browserArgs
