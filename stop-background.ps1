# Stop Group Agent started by start-background.ps1
$ErrorActionPreference = "SilentlyContinue"
Set-Location $PSScriptRoot

$pidFile = Join-Path $PSScriptRoot "data\server.pid"
$targets = New-Object System.Collections.Generic.HashSet[int]

if (Test-Path $pidFile) {
    $agentPid = (Get-Content $pidFile | Select-Object -First 1)
    if ($agentPid) {
        [void]$targets.Add([int]$agentPid)
    }
}

# The venv python.exe can be a launcher that spawns the real interpreter.
# The actual listener is the authoritative target.
$connections = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($connection in $connections) {
    [void]$targets.Add([int]$connection.OwningProcess)
}

$stopped = $false
foreach ($agentPid in $targets) {
    $process = Get-Process -Id $agentPid -ErrorAction SilentlyContinue
    if ($process) {
        # Do not use /T here: WeChat.exe is launched by the agent and would be
        # killed as a child process. WeChat keeps its login state and can be
        # reattached the next time the service starts.
        & taskkill.exe /F /PID $agentPid 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Stopped Group Agent process (PID $agentPid)."
            $stopped = $true
        } else {
            Stop-Process -Id $agentPid -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped Group Agent (PID $agentPid)."
            $stopped = $true
        }
    }
}

Remove-Item $pidFile -Force -ErrorAction SilentlyContinue

# The OpenCode sidecar is a child of the agent; taskkill /F above does not
# always reap it. Stop only the PID we recorded for our own sidecar.
$opencodePidFile = Join-Path $PSScriptRoot "data\opencode\server.pid"
if (Test-Path $opencodePidFile) {
    $opencodePid = (Get-Content $opencodePidFile | Select-Object -First 1)
    if ($opencodePid) {
        $opencodeProcess = Get-Process -Id ([int]$opencodePid) -ErrorAction SilentlyContinue
        if ($opencodeProcess) {
            Stop-Process -Id ([int]$opencodePid) -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped OpenCode sidecar (PID $opencodePid)."
        }
    }
    Remove-Item $opencodePidFile -Force -ErrorAction SilentlyContinue
}

# Give Windows a moment to release the port.
for ($i = 0; $i -lt 10; $i++) {
    $stillListening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if (-not $stillListening) { break }
    Start-Sleep -Milliseconds 300
}

if (-not $stopped) {
    Write-Host "Group Agent is not running."
}
