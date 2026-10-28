# Install the official OpenCode CLI from the upstream GitHub release.
# Source: https://github.com/anomalyco/opencode
# The Qunban runtime will prefer data/opencode/bin/opencode.exe.
param(
    [string]$Version = "1.19.4",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$targetDir = Join-Path $root "data\opencode\bin"
$target = Join-Path $targetDir "opencode.exe"

if ((Test-Path $target) -and -not $Force) {
    Write-Host "OpenCode already installed: $target"
    exit 0
}

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$archive = Join-Path $env:TEMP "opencode-windows-x64-$Version.zip"
$url = "https://github.com/anomalyco/opencode/releases/download/v$Version/opencode-windows-x64.zip"

Write-Host "Downloading official OpenCode $Version from GitHub..."
Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing

$extract = Join-Path $env:TEMP "opencode-$Version"
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
Expand-Archive -Path $archive -DestinationPath $extract -Force

$binary = Get-ChildItem -Path $extract -Recurse -Filter "opencode.exe" | Select-Object -First 1
if (-not $binary) {
    throw "opencode.exe was not found in the downloaded archive."
}
Copy-Item $binary.FullName $target -Force
Remove-Item $archive -Force -ErrorAction SilentlyContinue
Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue

& $target --version
Write-Host "Installed official OpenCode at $target"
