# Start the independently owned wechatauto transport service.
param(
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
$python = Join-Path $project ".venv312\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = Join-Path $project ".venv\Scripts\python.exe"
}
if (-not (Test-Path $python)) {
    Write-Error "Python virtualenv not found."
    exit 1
}
$outLog = Join-Path $project "data\wechatauto-service.out.log"
$errLog = Join-Path $project "data\wechatauto-service.err.log"
$pidFile = Join-Path $project "data\wechatauto-service.pid"
New-Item -ItemType Directory -Force -Path (Join-Path $project "data") | Out-Null

$existing = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing -and -not $Force) {
    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:8010/v1/status" -TimeoutSec 5
        if ($status.status.state -eq "online") {
            $existing.OwningProcess | Set-Content -Path $pidFile -Encoding ascii
            Write-Host "wechatauto service already online (PID $($existing.OwningProcess))."
            exit 0
        }
    } catch {
        Write-Host "Port 8010 is in use but the wechatauto service is not healthy."
        exit 1
    }
}

if ($existing -and $Force) {
    Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

$env:PYTHONIOENCODING = "utf-8"
$process = Start-Process -FilePath $python `
    -ArgumentList @((Join-Path $project "scripts\wechatauto_service.py"), "--backend", "replica", "--port", "8010") `
    -WorkingDirectory $project `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru
$process.Id | Set-Content -Path $pidFile -Encoding ascii

for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 700
    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:8010/v1/status" -TimeoutSec 3
        if ($status.status.state -eq "online") {
            Write-Host "wechatauto service online (PID $($status.pid))."
            exit 0
        }
    } catch {
    }
}

Write-Host "wechatauto service did not become online. Logs:"
if (Test-Path $errLog) { Get-Content $errLog -Tail 30 }
& taskkill.exe /F /T /PID $process.Id 2>$null | Out-Null
exit 1
