# Stop the independently owned wechatauto service.
$ErrorActionPreference = "Stop"
$connection = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $connection) {
    Write-Host "wechatauto service is not listening on 8010."
    exit 0
}
$pidValue = $connection.OwningProcess
& taskkill.exe /F /T /PID $pidValue 2>$null | Out-Null
Remove-Item -LiteralPath (Join-Path $PSScriptRoot "data\wechatauto-service.pid") -ErrorAction SilentlyContinue
Write-Host "wechatauto service stopped (PID $pidValue)."
