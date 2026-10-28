<#
    Start 群伴 with Administrator privileges.
    The service itself must be elevated for PowerShell commands to inherit
    admin rights without a UAC prompt on every call.
#>
param([switch]$Elevated)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin -and -not $Elevated) {
    Write-Host "Requesting administrator privileges (UAC)…"
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-Elevated"
    )
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $arguments
    exit 0
}

Write-Host "Starting Group Agent as Administrator…"
& (Join-Path $PSScriptRoot "stop-background.ps1")
Start-Sleep -Seconds 2
& (Join-Path $PSScriptRoot "start-background.ps1")
