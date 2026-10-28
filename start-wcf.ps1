# Start Group Agent with WeChatFerry. WeChat PC injection normally works in
# the current user session; this self-elevating entry point helps when the
# client reports an access-denied/init failure.
$ErrorActionPreference = "Stop"
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Requesting administrator privileges..."
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments
    exit 0
}

Set-Location $PSScriptRoot
& (Join-Path $PSScriptRoot "start-background.ps1")
