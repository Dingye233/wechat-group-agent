# Start Group Agent in the background (Windows PowerShell)
# -NoBrowser: do not open the dashboard automatically.
# -NoTray:    do not launch the tray window (used by tray.ps1 itself).
param(
    [switch]$NoBrowser,
    [switch]$NoTray
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Start-TrayWindow {
    if ($NoTray) { return }
    $trayScript = Join-Path $PSScriptRoot "tray.ps1"
    if (-not (Test-Path $trayScript)) { return }
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$trayScript`"" `
        -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden | Out-Null
}

$venvDir = Join-Path $PSScriptRoot ".venv"
if (Test-Path (Join-Path $PSScriptRoot ".venv312\Scripts\python.exe")) {
    $venvDir = Join-Path $PSScriptRoot ".venv312"
}
$python = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[setup] Creating virtual environment..."
    $base = Get-Command python -ErrorAction SilentlyContinue
    if (-not $base) {
        $base = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $base) {
        Write-Error "Python not found. Install Python 3.11+ first."
        exit 1
    }
    if ($base.Name -eq "py.exe") {
        & $base.Source -3 -m venv .venv
    } else {
        & $base.Source -m venv .venv
    }
    $venvDir = Join-Path $PSScriptRoot ".venv"
    $python = Join-Path $venvDir "Scripts\python.exe"
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.txt
}

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "data") | Out-Null
$pidFile = Join-Path $PSScriptRoot "data\server.pid"
$outLog = Join-Path $PSScriptRoot "data\server.out.log"
$errLog = Join-Path $PSScriptRoot "data\server.err.log"

# If production uses the split transport, ensure the independently owned
# wechatauto service is healthy before Qunban auto-login tries to reach it.
$autoLogin = ""
try {
    $dbPath = Join-Path $PSScriptRoot "data\agent.db"
    if (Test-Path $dbPath) {
        $autoLogin = & $python -c "import sqlite3,json; c=sqlite3.connect(r'$dbPath'); r=c.execute(\"select value from settings where key='auto_login'\").fetchone(); print(json.loads(r[0]) if r else '')" 2>$null
    }
} catch {
    $autoLogin = ""
}
if ($autoLogin -eq "replica-remote") {
    & (Join-Path $PSScriptRoot "start-wechatauto-service.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "wechatauto service failed; refusing to start Qunban in remote mode."
        exit 1
    }
}

# A stale launcher PID can survive while its child owns the port. Prefer the
# actual listener, and never start a second server on the same port.
$existingConnection = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existingConnection) {
    $existingPid = $existingConnection.OwningProcess
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3
        if ($health.ok) {
            $existingPid | Set-Content -Path $pidFile -Encoding ascii
            Write-Host "Group Agent is already running (PID $existingPid)."
            Write-Host "Open http://127.0.0.1:8000"
            if (-not $NoBrowser) { Start-Process "http://127.0.0.1:8000" }
            Start-TrayWindow
            exit 0
        }
    } catch {
        # fall through to the error below
    }
    Write-Host "Port 8000 is already in use by PID $existingPid, but it is not this service."
    Write-Host "Stop it first, or change the port in the start command."
    exit 1
}

if (Test-Path $pidFile) {
    $oldPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($oldPid) {
        $old = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($old) {
            Write-Host "Group Agent process exists (PID $oldPid) but port 8000 is not listening."
            Write-Host "Run stop-background.ps1, then try again."
            exit 1
        }
    }
}

$env:PYTHONIOENCODING = "utf-8"
$arguments = @(
    "-m", "uvicorn", "app.main:app",
    "--host", "127.0.0.1",
    "--port", "8000"
)
$process = Start-Process -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

$process.Id | Set-Content -Path $pidFile -Encoding ascii

$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3
        if ($health.ok) {
            $healthy = $true
            break
        }
    } catch {
        # service is still starting
    }
}

if ($healthy) {
    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $actualPid = if ($listener) { $listener.OwningProcess } else { $process.Id }
    $actualPid | Set-Content -Path $pidFile -Encoding ascii
    Write-Host "Group Agent started (PID $actualPid)."
    Write-Host "Dashboard: http://127.0.0.1:8000"
    Write-Host "Logs: $(Join-Path $PSScriptRoot 'data\server.log')"
    Write-Host "Process output: $outLog"
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:8000" }
    Start-TrayWindow
    exit 0
}

Write-Host "Service did not respond within 15 seconds. Check logs:"
if (Test-Path $errLog) { Get-Content $errLog -Tail 30 }
& taskkill.exe /F /T /PID $process.Id 2>$null | Out-Null
Write-Host "PID file: $pidFile"
exit 1
