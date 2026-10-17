# run_report_regen_2330.ps1
# Fallback report regeneration (report-only, no collection, no browser).
# Purpose: safety net when the 20:30 prematch chain dies (e.g. account freeze) —
# regenerate tomorrow..+2 pre-match reports from whatever data has been collected.
# Today's matches are deliberately NOT re-rendered at 23:30 (live/finished, audit trail locked).
# C-20261017-126: propagate python exit code + write chain_status heartbeat/result
# (watchdog uses this to detect "20:30 chain failure masked by 23:30 fallback").
$ErrorActionPreference = 'Continue'
$root = 'f:\zuqiu\五大联赛专属模型\五大联赛专属模型'
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$py = 'C:\Python314\python.exe'
$now = Get-Date
$target = $now.AddDays(1).ToString('yyyy-MM-dd')
$stamp = $now.ToString('yyyyMMdd')
$log = Join-Path $root "logs\report_regen_${stamp}_2330.log"

# C-20261017-126: 心跳/结果状态文件（无 BOM 写入，Python json 直读）
$statusDir = Join-Path $root 'logs\chain_status'
if (-not (Test-Path -LiteralPath $statusDir)) { New-Item -ItemType Directory -Path $statusDir | Out-Null }
$statusFile = Join-Path $statusDir "report_regen_2330_${stamp}.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$chainStatus = [ordered]@{
    chain       = 'report_regen_2330'
    chain_date  = $now.ToString('yyyy-MM-dd')
    started_at  = $now.ToString('yyyy-MM-dd HH:mm:ss')
    finished_at = $null
    exit_code   = $null
    steps       = @()
}
[System.IO.File]::WriteAllText($statusFile, ($chainStatus | ConvertTo-Json -Depth 6 -Compress), $utf8NoBom)

"=== report regen 2330 start $($now.ToString('yyyy-MM-dd HH:mm:ss')) target=$target days=2 ===" | Out-File -FilePath $log -Encoding utf8
& $py -u scripts\generate_unified_report.py --date $target --days 2 *>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
"=== report regen 2330 end $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') exit=$code ===" | Out-File -FilePath $log -Append -Encoding utf8

$chainStatus.steps += [pscustomobject]@{ step = 'regen_报告重生成'; exit_code = $code }
$chainStatus.finished_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
$chainStatus.exit_code = $code
[System.IO.File]::WriteAllText($statusFile, ($chainStatus | ConvertTo-Json -Depth 6 -Compress), $utf8NoBom)

# best-effort 告警（全隔离，不影响退出码传播）
try {
    & $py -u (Join-Path $root 'scripts\chain_alert.py') inspect-chain --result $statusFile *>&1 |
        Out-File -FilePath $log -Append -Encoding utf8
} catch {
    "[告警器调用异常，已隔离] $_" | Out-File -FilePath $log -Append -Encoding utf8
}
exit $code
