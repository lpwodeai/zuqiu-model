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

# 步骤执行追踪（C-20260926-100）：每步记录退出码，报告生成失败时 exit 1，任务结果可监控
$script:stepResults = @()
function Invoke-Step {
    param([string]$Name, [string[]]$Arguments)
    "`n[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] >>> $Name" | Out-File -FilePath $logFile -Append -Encoding utf8
    & $py @Arguments *>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
    $code = $LASTEXITCODE
    $script:stepResults += [pscustomobject]@{ Step = $Name; ExitCode = $code }
    $mark = if ($code -eq 0) { '[OK]' } else { '[FAIL]' }
    "[$(Get-Date -Format 'HH:mm:ss')] $mark $Name (exit=$code)" | Out-File -FilePath $logFile -Append -Encoding utf8
}

# 0a. 赛前数据补采：SofaScore 赛程注册 + 球队实力特征重算（500.com 待采集器升级后加入）
Invoke-Step -Name "0a_赛前数据补采" -Arguments @("collection\final_sofascore_collector.py", "--leagues", "all", "--season", "26/27", "--rounds", "5")
# 0a-1. 赛前官方首发/伤停采集（预测首发 XI + missingPlayers 刷新）
Invoke-Step -Name "0a-1_赛前官方首发伤停采集" -Arguments @("collection\prematch_sofascore_lineups.py", "--date", $reportDate, "--days-ahead", "2")
Invoke-Step -Name "0a-2_伤停同步入库" -Arguments @("scripts\player_injury_source.py", "--sync-sofascore")
Invoke-Step -Name "0a-3_赛前特征重算" -Arguments @("features\sofascore_pre_match_features.py", "--n-recent", "5")

# 0b. 回踩当日竞彩时序赔率（刷新未赛赔率快照；INSERT OR IGNORE，新增快照不覆盖旧值）
Invoke-Step -Name "0b_竞彩时序赔率回踩" -Arguments @("scripts\sporttery_live_collector.py", "--date", $reportDate)

# 0c. C-20260919-019: 重建 Elo 快照（归一化去重+别名英文键），防静态快照随比赛累积过期
Invoke-Step -Name "0c_Elo快照重建" -Arguments @("scripts\deploy_t005v2_final.py", "--elo-only")

# 1. 生成预测报告（内置完整性门禁：竞彩WDL快照<2 会在汇总中标记「数据完整性告警」）
Invoke-Step -Name "1_生成预测报告" -Arguments @("scripts\generate_unified_report.py", "--date", $reportDate, "--days", "2")

# 2. 闭环校验：逐比赛日检查汇总是否含需回踩的缺失告警（缺漂移/无时序；「未开售WDL」为玩法缺位，不触发），明确提示运营
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

# 3. 步骤结果汇总 + 失败传播（C-20260926-100）：报告生成失败 → exit 1，计划任务结果可监控
"`n===== 步骤执行结果 =====" | Out-File -FilePath $logFile -Append -Encoding utf8
($script:stepResults | Format-Table -AutoSize | Out-String) | Out-File -FilePath $logFile -Append -Encoding utf8
$reportStep = $script:stepResults | Where-Object { $_.Step -eq '1_生成预测报告' }
if ($null -ne $reportStep -and $reportStep.ExitCode -ne 0) {
    "[FAIL] 预测报告生成失败 (exit=$($reportStep.ExitCode))，任务标记为失败" | Out-File -FilePath $logFile -Append -Encoding utf8
    exit 1
}
exit 0