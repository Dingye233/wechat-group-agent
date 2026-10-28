# Qunban background controller (modern dark tray window).
# Closing the window follows the web setting tray_close_action:
#   tray -> hide to the notification area
#   exit -> stop the service and quit
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# One controller per user session.  A second launch asks the existing window
# to come back instead of silently doing nothing.
$mutex = New-Object System.Threading.Mutex($false, "Local\QunbanTray")
if (-not $mutex.WaitOne(0)) {
    try {
        $existing = [System.Threading.EventWaitHandle]::OpenExisting("Local\QunbanTrayShow")
        [void]$existing.Set()
        $existing.Dispose()
    } catch {
    }
    exit 0
}
$showEvent = New-Object System.Threading.EventWaitHandle(
    $false,
    [System.Threading.EventResetMode]::AutoReset,
    "Local\QunbanTrayShow"
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class QunbanUi {
  [DllImport("user32.dll")] public static extern bool ReleaseCapture();
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, int Msg, IntPtr wParam, IntPtr lParam);
  [DllImport("gdi32.dll")] public static extern IntPtr CreateRoundRectRgn(int left, int top, int right, int bottom, int width, int height);
}
"@

$serviceUrl = "http://127.0.0.1:8000"
$startScript = Join-Path $root "start-background.ps1"
$stopScript = Join-Path $root "stop-background.ps1"
$iconPath = Join-Path $root "frontend\qunban-icon.ico"
$logFile = Join-Path $root "data\tray.log"
$script:allowClose = $false
$script:stopping = $false
$script:userHidden = $false

function Write-TrayLog([string]$message) {
    try {
        $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message
        Add-Content -Path $logFile -Value $line -Encoding utf8
    } catch {
    }
}

Write-TrayLog "tray process started; pid=$PID"

function Test-Qunban {
    try {
        $health = Invoke-RestMethod -Uri "$serviceUrl/api/health" -TimeoutSec 2
        return [bool]$health.ok
    } catch {
        return $false
    }
}

function Get-CloseAction {
    try {
        $data = Invoke-RestMethod -Uri "$serviceUrl/api/settings" -TimeoutSec 2
        if ($data.settings.tray_close_action -eq "exit") { return "exit" }
    } catch {
        # The service may be down; hiding is always safe.
    }
    return "tray"
}

function Start-Qunban {
    $script:stopping = $false
    if (Test-Qunban) { return $true }
    Write-TrayLog "starting service"
    try {
        & $startScript -NoBrowser -NoTray | Out-Null
        for ($i = 0; $i -lt 40; $i++) {
            if (Test-Qunban) { return $true }
            Start-Sleep -Milliseconds 500
        }
    } catch {
        Write-TrayLog ("start error: " + $_.Exception.Message)
        return $false
    }
    return (Test-Qunban)
}

function Stop-Qunban {
    if ($script:stopping) { return }
    $script:stopping = $true
    Write-TrayLog "stopping service"
    try {
        & $stopScript | Out-Null
    } catch {
        Write-TrayLog ("stop error: " + $_.Exception.Message)
    }
}

function Set-RoundedRegion($control, [int]$radius) {
    try {
        $region = [System.Drawing.Region]::FromHrgn(
            [QunbanUi]::CreateRoundRectRgn(
                0, 0, $control.Width + 1, $control.Height + 1, $radius, $radius
            )
        )
        $control.Region = $region
    } catch {
    }
}

$icon = [System.Drawing.SystemIcons]::Application
if (Test-Path $iconPath) {
    try {
        $icon = New-Object System.Drawing.Icon($iconPath)
    } catch {
        $icon = [System.Drawing.SystemIcons]::Application
    }
}

$bg = [System.Drawing.Color]::FromArgb(10, 16, 14)
$panelBg = [System.Drawing.Color]::FromArgb(18, 29, 24)
$line = [System.Drawing.Color]::FromArgb(35, 54, 45)
$text = [System.Drawing.Color]::FromArgb(238, 246, 241)
$muted = [System.Drawing.Color]::FromArgb(142, 160, 151)
$green = [System.Drawing.Color]::FromArgb(74, 222, 128)
$amber = [System.Drawing.Color]::FromArgb(251, 191, 36)
$red = [System.Drawing.Color]::FromArgb(248, 113, 113)
$buttonBg = [System.Drawing.Color]::FromArgb(25, 40, 33)

$form = New-Object System.Windows.Forms.Form
$form.Text = "群伴后台服务"
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form.StartPosition = "CenterScreen"
$form.ClientSize = New-Object System.Drawing.Size(560, 340)
$form.BackColor = $bg
$form.ForeColor = $text
$form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ShowInTaskbar = $true
$form.Icon = $icon

$accent = New-Object System.Windows.Forms.Panel
$accent.Location = New-Object System.Drawing.Point(0, 0)
$accent.Size = New-Object System.Drawing.Size(560, 3)
$accent.BackColor = $green
$form.Controls.Add($accent)

$header = New-Object System.Windows.Forms.Panel
$header.Location = New-Object System.Drawing.Point(0, 3)
$header.Size = New-Object System.Drawing.Size(560, 66)
$header.BackColor = $panelBg
$form.Controls.Add($header)

$title = New-Object System.Windows.Forms.Label
$title.Text = "群伴后台服务"
$title.ForeColor = $text
$title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 14, [System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.BackColor = [System.Drawing.Color]::Transparent
$title.Location = New-Object System.Drawing.Point(24, 13)
$header.Controls.Add($title)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = "WECHAT GROUP AGENT"
$subtitle.ForeColor = $muted
$subtitle.Font = New-Object System.Drawing.Font("Segoe UI", 7.5)
$subtitle.AutoSize = $true
$subtitle.BackColor = [System.Drawing.Color]::Transparent
$subtitle.Location = New-Object System.Drawing.Point(26, 42)
$header.Controls.Add($subtitle)

$minButton = New-Object System.Windows.Forms.Label
$minButton.Text = "—"
$minButton.ForeColor = $muted
$minButton.Font = New-Object System.Drawing.Font("Segoe UI", 12)
$minButton.TextAlign = "MiddleCenter"
$minButton.Size = New-Object System.Drawing.Size(28, 26)
$minButton.Location = New-Object System.Drawing.Point(485, 14)
$minButton.Cursor = [System.Windows.Forms.Cursors]::Hand
$minButton.BackColor = [System.Drawing.Color]::Transparent
$minButton.Add_Click({ $script:userHidden = $true; $form.Hide() })
$minButton.Add_MouseEnter({ $minButton.ForeColor = $text })
$minButton.Add_MouseLeave({ $minButton.ForeColor = $muted })
$header.Controls.Add($minButton)

$closeButton = New-Object System.Windows.Forms.Label
$closeButton.Text = "✕"
$closeButton.ForeColor = $muted
$closeButton.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$closeButton.TextAlign = "MiddleCenter"
$closeButton.Size = New-Object System.Drawing.Size(28, 26)
$closeButton.Location = New-Object System.Drawing.Point(518, 14)
$closeButton.Cursor = [System.Windows.Forms.Cursors]::Hand
$closeButton.BackColor = [System.Drawing.Color]::Transparent
$closeButton.Add_Click({ $form.Close() })
$closeButton.Add_MouseEnter({
    $closeButton.ForeColor = [System.Drawing.Color]::White
    $closeButton.BackColor = $red
})
$closeButton.Add_MouseLeave({
    $closeButton.ForeColor = $muted
    $closeButton.BackColor = [System.Drawing.Color]::Transparent
})
$header.Controls.Add($closeButton)

$drag = {
    param($sender, $eventArgs)
    if ($eventArgs.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        [void][QunbanUi]::ReleaseCapture()
        [void][QunbanUi]::SendMessage($form.Handle, 0xA1, [IntPtr]2, [IntPtr]::Zero)
    }
}
$header.Add_MouseDown($drag)
$title.Add_MouseDown($drag)
$subtitle.Add_MouseDown($drag)

$statusCard = New-Object System.Windows.Forms.Panel
$statusCard.Location = New-Object System.Drawing.Point(24, 86)
$statusCard.Size = New-Object System.Drawing.Size(512, 92)
$statusCard.BackColor = $panelBg
$form.Controls.Add($statusCard)
Set-RoundedRegion $statusCard 16

$dot = New-Object System.Windows.Forms.Label
$dot.Text = "●"
$dot.ForeColor = $amber
$dot.Font = New-Object System.Drawing.Font("Segoe UI", 13)
$dot.AutoSize = $false
$dot.Size = New-Object System.Drawing.Size(22, 24)
$dot.Location = New-Object System.Drawing.Point(16, 16)
$dot.BackColor = [System.Drawing.Color]::Transparent
$statusCard.Controls.Add($dot)

$statusText = New-Object System.Windows.Forms.Label
$statusText.Text = "正在检查服务状态…"
$statusText.ForeColor = $text
$statusText.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 12, [System.Drawing.FontStyle]::Bold)
$statusText.AutoSize = $false
$statusText.Size = New-Object System.Drawing.Size(450, 26)
$statusText.Location = New-Object System.Drawing.Point(42, 13)
$statusText.BackColor = [System.Drawing.Color]::Transparent
$statusCard.Controls.Add($statusText)

$statusUrl = New-Object System.Windows.Forms.Label
$statusUrl.Text = "http://127.0.0.1:8000"
$statusUrl.ForeColor = $muted
$statusUrl.Font = New-Object System.Drawing.Font("Segoe UI", 8.5)
$statusUrl.AutoSize = $true
$statusUrl.Location = New-Object System.Drawing.Point(43, 41)
$statusUrl.BackColor = [System.Drawing.Color]::Transparent
$statusCard.Controls.Add($statusUrl)

$statusDetail = New-Object System.Windows.Forms.Label
$statusDetail.Text = "关闭窗口默认隐藏到右下角托盘"
$statusDetail.ForeColor = $muted
$statusDetail.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 8)
$statusDetail.AutoSize = $false
$statusDetail.Size = New-Object System.Drawing.Size(450, 20)
$statusDetail.Location = New-Object System.Drawing.Point(43, 62)
$statusDetail.BackColor = [System.Drawing.Color]::Transparent
$statusCard.Controls.Add($statusDetail)

function Set-FlatButton($button, $normal, $hover, $fore, [bool]$primary = $false) {
    $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $button.FlatAppearance.BorderSize = 0
    $button.BackColor = $normal
    $button.ForeColor = $fore
    $button.Cursor = [System.Windows.Forms.Cursors]::Hand
    $button.Font = New-Object System.Drawing.Font(
        "Microsoft YaHei UI", 9, $(if ($primary) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular })
    )
    $button.Add_MouseEnter({ $button.BackColor = $hover }.GetNewClosure())
    $button.Add_MouseLeave({ $button.BackColor = $normal }.GetNewClosure())
}

$openButton = New-Object System.Windows.Forms.Button
$openButton.Text = "打开控制台"
$openButton.Location = New-Object System.Drawing.Point(24, 196)
$openButton.Size = New-Object System.Drawing.Size(146, 40)
$openButton.Add_Click({ Start-Process $serviceUrl })
$form.Controls.Add($openButton)
Set-FlatButton $openButton ([System.Drawing.Color]::FromArgb(34, 197, 94)) ([System.Drawing.Color]::FromArgb(52, 211, 153)) ([System.Drawing.Color]::FromArgb(5, 30, 18)) $true
Set-RoundedRegion $openButton 12

$restartButton = New-Object System.Windows.Forms.Button
$restartButton.Text = "重启服务"
$restartButton.Location = New-Object System.Drawing.Point(184, 196)
$restartButton.Size = New-Object System.Drawing.Size(118, 40)
$restartButton.Add_Click({
    $statusText.Text = "正在重启服务…"
    $dot.ForeColor = $amber
    [System.Windows.Forms.Application]::DoEvents()
    Stop-Qunban
    Start-Sleep -Milliseconds 400
    if (Start-Qunban) {
        $dot.ForeColor = $green
        $statusText.Text = "服务运行中"
        $statusDetail.Text = "已重启 · 关闭窗口按网页设置处理"
    } else {
        $dot.ForeColor = $red
        $statusText.Text = "服务启动失败"
        $statusDetail.Text = "请查看 data\server.err.log"
    }
})
$form.Controls.Add($restartButton)
Set-FlatButton $restartButton $buttonBg ([System.Drawing.Color]::FromArgb(37, 57, 47)) $text
Set-RoundedRegion $restartButton 12

$hideButton = New-Object System.Windows.Forms.Button
$hideButton.Text = "隐藏到托盘"
$hideButton.Location = New-Object System.Drawing.Point(314, 196)
$hideButton.Size = New-Object System.Drawing.Size(118, 40)
$hideButton.Add_Click({ $script:userHidden = $true; $form.Hide() })
$form.Controls.Add($hideButton)
Set-FlatButton $hideButton $buttonBg ([System.Drawing.Color]::FromArgb(37, 57, 47)) $text
Set-RoundedRegion $hideButton 12

$exitButton = New-Object System.Windows.Forms.Button
$exitButton.Text = "退出"
$exitButton.Location = New-Object System.Drawing.Point(444, 196)
$exitButton.Size = New-Object System.Drawing.Size(92, 40)
$exitButton.Add_Click({
    Stop-Qunban
    $script:allowClose = $true
    $notify.Visible = $false
    $form.Close()
})
$form.Controls.Add($exitButton)
Set-FlatButton $exitButton ([System.Drawing.Color]::FromArgb(42, 26, 26)) ([System.Drawing.Color]::FromArgb(70, 32, 32)) $red
Set-RoundedRegion $exitButton 12

$hint = New-Object System.Windows.Forms.Label
$hint.Text = "关闭窗口默认隐藏到右下角托盘，右键托盘图标可退出。`r`n关闭行为可在网页「设置 → 关闭后台窗口时」修改；Alt+P 可暂停 Agent。"
$hint.ForeColor = $muted
$hint.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 8.5)
$hint.AutoSize = $false
$hint.Size = New-Object System.Drawing.Size(512, 48)
$hint.Location = New-Object System.Drawing.Point(24, 254)
$hint.BackColor = [System.Drawing.Color]::Transparent
$form.Controls.Add($hint)

$footer = New-Object System.Windows.Forms.Label
$footer.Text = "群伴 · WeChat Group Agent"
$footer.ForeColor = [System.Drawing.Color]::FromArgb(96, 113, 104)
$footer.Font = New-Object System.Drawing.Font("Segoe UI", 7.5)
$footer.AutoSize = $true
$footer.Location = New-Object System.Drawing.Point(24, 314)
$footer.BackColor = [System.Drawing.Color]::Transparent
$form.Controls.Add($footer)

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $icon
$notify.Text = "群伴后台服务"
$notify.Visible = $true
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$menu.ShowImageMargin = $false
$showItem = $menu.Items.Add("显示后台窗口")
$showItem.Add_Click({ $script:userHidden = $false; $form.Show(); $form.WindowState = "Normal"; $form.Activate() })
$openItem = $menu.Items.Add("打开群伴控制台")
$openItem.Add_Click({ Start-Process $serviceUrl })
$pauseItem = $menu.Items.Add("暂停 Agent（Alt+P）")
$pauseItem.Add_Click({
    try {
        $reason = [System.Uri]::EscapeDataString("从托盘图标暂停自动操作")
        Invoke-RestMethod -Method Post -Uri "$serviceUrl/api/control/detach?reason=$reason" -TimeoutSec 3 | Out-Null
    } catch {
    }
    $script:userHidden = $false
    $form.Show()
    $form.WindowState = "Normal"
    $form.Activate()
})
[void]$menu.Items.Add("-")
$restartItem = $menu.Items.Add("重启服务")
$restartItem.Add_Click({
    $script:userHidden = $false
    $form.Show()
    $form.WindowState = "Normal"
    $form.Activate()
    $restartButton.PerformClick()
})
[void]$menu.Items.Add("-")
$exitItem = $menu.Items.Add("退出群伴")
$exitItem.Add_Click({
    Stop-Qunban
    $script:allowClose = $true
    $notify.Visible = $false
    $form.Close()
})
$notify.ContextMenuStrip = $menu
$notify.Add_DoubleClick({
    $script:userHidden = $false
    $form.Show()
    $form.WindowState = "Normal"
    $form.Activate()
})

$form.Add_Shown({
    Set-RoundedRegion $form 20
    if (Start-Qunban) {
        $dot.ForeColor = $green
        $statusText.Text = "服务运行中"
        $statusDetail.Text = "关闭窗口按网页设置处理 · 右键托盘可退出"
    } else {
        $dot.ForeColor = $red
        $statusText.Text = "服务未运行"
        $statusDetail.Text = "点击「重启服务」或查看 data\server.err.log"
    }
})

$form.Add_FormClosing({
    param($sender, $eventArgs)
    if ($script:allowClose) { return }
    $action = Get-CloseAction
    Write-TrayLog "window close requested; action=$action"
    if ($action -eq "exit") {
        Stop-Qunban
        $script:allowClose = $true
        $notify.Visible = $false
        return
    }
    $eventArgs.Cancel = $true
    $script:userHidden = $true
    $form.Hide()
    try {
        $notify.ShowBalloonTip(3000, "群伴仍在运行", "窗口已隐藏到右下角托盘；右键托盘图标可退出。", [System.Windows.Forms.ToolTipIcon]::Info)
    } catch {
    }
})

$startupTimer = New-Object System.Windows.Forms.Timer
$startupTimer.Interval = 700
$startupTimer.Add_Tick({
    $startupTimer.Stop()
    $script:userHidden = $false
    if (-not $form.Visible) { $form.Show() }
    $form.WindowState = "Normal"
    $form.Activate()
    Write-TrayLog "startup window visible=$($form.Visible)"
})
$startupTimer.Start()

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$showTimer = New-Object System.Windows.Forms.Timer
$showTimer.Interval = 500
$showTimer.Add_Tick({
    if ($showEvent.WaitOne(0)) {
        $script:userHidden = $false
    }
    if (-not $script:userHidden -and -not $form.Visible) {
        $form.Show()
        $form.WindowState = "Normal"
    }
})
$showTimer.Start()

$timer.Add_Tick({
    if (-not $script:userHidden -and -not $form.Visible) {
        $form.Show()
        $form.WindowState = "Normal"
    }
    if (Test-Qunban) {
        if ($statusText.Text -ne "正在重启服务…") {
            $dot.ForeColor = $green
            $statusText.Text = "服务运行中"
            $statusDetail.Text = "关闭窗口按网页设置处理 · 右键托盘可退出"
        }
    } else {
        $dot.ForeColor = $red
        $statusText.Text = "服务未运行"
        $statusDetail.Text = "点击「重启服务」或查看 data\server.err.log"
    }
})
$timer.Start()

[System.Windows.Forms.Application]::Run($form)
$startupTimer.Stop()
$timer.Stop()
$showTimer.Stop()
$notify.Visible = $false
$notify.Dispose()
$showEvent.Dispose()
$form.Dispose()
$mutex.ReleaseMutex()
$mutex.Dispose()
