$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$log = Join-Path $root "data\wcf_probe.log"
"probe wrapper started $(Get-Date -Format o)" | Set-Content -Path $log -Encoding utf8
& (Join-Path $root ".venv312\Scripts\python.exe") (Join-Path $root "scripts\wcf_probe.py") *>> $log
$code = $LASTEXITCODE
"probe exit code: $code" | Add-Content -Path $log -Encoding utf8
