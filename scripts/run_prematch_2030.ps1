$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$logDir = Join-Path $root "logs"
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ("prematch_report_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
$py = "C:\Python314\python.exe"
# 报告日期改为运行时取当日（修复原硬编码 "2026-08-30" 导致报告停更的问题）
$reportDate = (Get-Date).ToString("yyyy-MM-dd")

# 0a. 赛前数据补采：SofaScore 赛程注册 + 球队实力特征重算（500.com 待采集器升级后加入）
& $py "collection\final_sofascore_collector.py" --leagues all --season 26/27 --rounds 5 *>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
& $py "features\sofascore_pre_match_features.py" --n-recent 5 *>&1 | Out-File -FilePath $logFile -Append -Encoding utf8

# 0b. 回踩当日竞彩时序赔率（刷新未赛赔率快照；INSERT OR IGNORE，新增快照不覆盖旧值）
& $py "scripts\sporttery_live_collector.py" --date $reportDate *>&1 | Out-File -FilePath $logFile -Encoding utf8

# 1. 生成预测报告（内置完整性门禁：竞彩WDL快照<2 会在汇总中标记「数据完整性告警」）
& $py "scripts\generate_unified_report.py" --date $reportDate --days 2 *>&1 | Out-File -FilePath $logFile -Append -Encoding utf8

# 2. 邮件推送（若汇总含需回踩的缺失告警（缺漂移/无时序），主题自动加 [缺数据] 红标；「未开售WDL」为玩法缺位不计入）
& $py "scripts\send_report_email.py" --date $reportDate --days 2 *>&1 | Out-File -FilePath $logFile -Append -Encoding utf8

# 3. 闭环校验：逐比赛日检查汇总是否含需回踩的缺失告警（缺漂移/无时序；「未开售WDL」为玩法缺位，不触发），明确提示运营
$hasAlert = $false
foreach ($d in @((Get-Date).ToString("yyyy-MM-dd"), (Get-Date).AddDays(1).ToString("yyyy-MM-dd"))) {
    $summary = Join-Path $root ("docs\prematch_reports\" + $d.Replace("-", "") + "\_summary.md")
    if ((Test-Path -LiteralPath $summary) -and (Select-String -LiteralPath $summary -Pattern "仅1条快照缺漂移|无竞彩WDL时序" -Quiet)) {
        $hasAlert = $true
    }
}
if ($hasAlert) {
    Write-Output "[告警] 汇总含竞彩WDL快照不足场次（缺漂移/无时序），需回踩补采（--no-skip-existing）。" | Out-File -FilePath $logFile -Append -Encoding utf8
}
exit 0