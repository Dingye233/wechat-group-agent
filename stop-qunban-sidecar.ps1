# Stop the Qunban sidecar.
$connection = Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $connection) { Write-Host "Qunban sidecar is not listening on 8020."; exit 0 }
& taskkill.exe /F /T /PID $connection.OwningProcess 2>$null | Out-Null
Remove-Item -LiteralPath (Join-Path $PSScriptRoot "data\qunban-sidecar.pid") -ErrorAction SilentlyContinue
Write-Host "Qunban sidecar stopped (PID $($connection.OwningProcess))."
