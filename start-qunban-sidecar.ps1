# Start the Qunban sidecar (memory/knowledge/context reads).
param([switch]$Force)
$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
$python = Join-Path $project ".venv312\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = Join-Path $project ".venv\Scripts\python.exe" }
if (-not (Test-Path $python)) { Write-Error "Python venv not found"; exit 1 }
New-Item -ItemType Directory -Force -Path (Join-Path $project "data") | Out-Null
$pidFile = Join-Path $project "data\qunban-sidecar.pid"
$existing = Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing -and -not $Force) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8020/v1/health" -TimeoutSec 3
        if ($health.ok) {
            $existing.OwningProcess | Set-Content -Path $pidFile -Encoding ascii
            Write-Host "Qunban sidecar already online (PID $($existing.OwningProcess))."
            exit 0
        }
    } catch {
        Write-Host "Port 8020 is occupied but the sidecar is unhealthy."
        exit 1
    }
}
if ($existing -and $Force) {
    Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
$env:PYTHONIOENCODING = "utf-8"
$process = Start-Process -FilePath $python `
    -ArgumentList @((Join-Path $project "scripts\qunban_sidecar.py")) `
    -WorkingDirectory $project -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $project "data\qunban-sidecar.out.log") `
    -RedirectStandardError (Join-Path $project "data\qunban-sidecar.err.log") `
    -PassThru
$process.Id | Set-Content -Path $pidFile -Encoding ascii
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8020/v1/health" -TimeoutSec 2
        if ($health.ok) {
            Write-Host "Qunban sidecar online (PID $($health.pid))."
            exit 0
        }
    } catch {
    }
}
Write-Host "Qunban sidecar did not become healthy."
if (Test-Path (Join-Path $project "data\qunban-sidecar.err.log")) {
    Get-Content (Join-Path $project "data\qunban-sidecar.err.log") -Tail 30
}
exit 1
