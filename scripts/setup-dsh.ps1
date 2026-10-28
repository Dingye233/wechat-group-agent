# DeepSeek Harness runtime setup / update (Windows PowerShell)
# Versions come from the GitHub repository's dsh-v* tags; the matching official
# npm package is installed under data/dsh/runtimes/<version>.
param(
    [ValidateSet("stable", "alpha")]
    [string]$Channel = "stable",
    [switch]$Check,
    [switch]$Status
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venv = Join-Path $PSScriptRoot ".venv312"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    $venv = Join-Path $PSScriptRoot ".venv"
}
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Python virtual environment not found. Run start.bat once first."
    exit 1
}

$arguments = @("scripts\setup_dsh.py", "--channel", $Channel)
if ($Check) { $arguments += "--check" }
if ($Status) { $arguments += "--status" }
& $python @arguments
exit $LASTEXITCODE
