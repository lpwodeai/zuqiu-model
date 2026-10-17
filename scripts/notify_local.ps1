# notify_local.ps1 — C-20261017-126 本地桌面气泡通知（best-effort）
# 用法: powershell -File notify_local.ps1 -Title "标题" -Message "正文"
# 非交互会话（无桌面站）时静默失败，不抛错；文件物证才是告警 SSOT。
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [string]$Message = ""
)
try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    Add-Type -AssemblyName System.Drawing -ErrorAction Stop
    $ni = New-Object System.Windows.Forms.NotifyIcon
    $ni.Icon = [System.Drawing.SystemIcons]::Warning
    $ni.BalloonTipTitle = $Title
    $ni.BalloonTipText = $Message
    $ni.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Warning
    $ni.Visible = $true
    $ni.ShowBalloonTip(30000)
    Start-Sleep -Seconds 32
    $ni.Dispose()
} catch {
    # 桌面不可用属预期场景（计划任务非交互会话），静默退出 0
    exit 0
}
exit 0
