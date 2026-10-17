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

# C-20261017-126: 链心跳/结果状态文件（看门狗独立读取；无 BOM 写入，Python json 直读）
$statusDir = Join-Path $root "logs\chain_status"
if (-not (Test-Path -LiteralPath $statusDir)) { New-Item -ItemType Directory -Path $statusDir | Out-Null }
$statusFile = Join-Path $statusDir ("prematch_2030_" + $reportDate.Replace("-", "") + ".json")
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:chainStatus = [ordered]@{
    chain       = 'prematch_2030'
    chain_date  = $reportDate
    started_at  = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    finished_at = $null
    exit_code   = $null
    steps       = @()
}
function Write-ChainStatus {
    $json = ($script:chainStatus | ConvertTo-Json -Depth 6 -Compress)
    [System.IO.File]::WriteAllText($statusFile, $json, $utf8NoBom)
}
Write-ChainStatus   # 首行尽早落心跳：解析成功即存在（解析阶段死亡由看门狗 no_heartbeat 抓）

function Invoke-Step {
    param([string]$Name, [string[]]$Arguments)
    "`n[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] >>> $Name" | Out-File -FilePath $logFile -Append -Encoding utf8
    & $py -u @Arguments *>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
    $code = $LASTEXITCODE
    $script:stepResults += [pscustomobject]@{ Step = $Name; ExitCode = $code }
    $script:chainStatus.steps += [pscustomobject]@{ step = $Name; exit_code = $code }
    Write-ChainStatus
    $mark = if ($code -eq 0) { '[OK]' } else { '[FAIL]' }
    "[$(Get-Date -Format 'HH:mm:ss')] $mark $Name (exit=$code)" | Out-File -FilePath $logFile -Append -Encoding utf8
}

# 0a. 赛前数据补采：SofaScore 赛程注册 + 球队实力特征重算（500.com 待采集器升级后加入）
# C-20260926-103: 用日期窗口自动选轮（--upcoming-window 3），替代写死的 --rounds 5；
# SofaScore 轮次号与自然日不严格对应（国际比赛日拆轮/延期比赛留在原轮）。
Invoke-Step -Name "0a_赛前数据补采" -Arguments @("collection\final_sofascore_collector.py", "--leagues", "all", "--season", "26/27", "--upcoming-window", "3")
# 0a-1. 赛前官方首发/伤停采集（预测首发 XI + missingPlayers 刷新）
Invoke-Step -Name "0a-1_赛前官方首发伤停采集" -Arguments @("collection\prematch_sofascore_lineups.py", "--date", $reportDate, "--days-ahead", "2")
Invoke-Step -Name "0a-2_伤停同步入库" -Arguments @("scripts\player_injury_source.py", "--sync-sofascore")
# 0a-3. C-20261006-022 空档闸门：未来3天无注册赛程则跳过全量特征重算（历史耗时 1.5~2.7h；国际比赛日空档空转白烧 CPU/内存）
$upcomingN = $null
try {
    $gatePy = @'
import os, sqlite3
c = sqlite3.connect(os.environ["ODDS_DB_GATE"])
print(c.execute("SELECT COUNT(*) FROM fbref_match_mapping WHERE substr(match_date,1,10)>=date('now') AND substr(match_date,1,10)<date('now','+3 days')").fetchone()[0])
'@
    $env:ODDS_DB_GATE = Join-Path $root "data\odds.db"
    $upcomingN = ($gatePy | & $py -u - 2>$null | Select-Object -Last 1)
} catch { $upcomingN = $null }
$featureSkipFlag = Join-Path $root "data\.skip_feature_rebuild"
if (Test-Path -LiteralPath $featureSkipFlag) {
    "[$(Get-Date -Format 'HH:mm:ss')] [SKIP] 0a-3_赛前特征重算（人工跳过标记 data\.skip_feature_rebuild，C-20261011-033）" | Out-File -FilePath $logFile -Append -Encoding utf8
    $script:stepResults += [pscustomobject]@{ Step = "0a-3_赛前特征重算"; ExitCode = "SKIP-FLAG" }
    $script:chainStatus.steps += [pscustomobject]@{ step = "0a-3_赛前特征重算"; exit_code = "SKIP-FLAG" }
    Write-ChainStatus
}
elseif ("$upcomingN" -match '^\d+$' -and [int]"$upcomingN" -eq 0) {
    "[$(Get-Date -Format 'HH:mm:ss')] [SKIP] 0a-3_赛前特征重算（未来3天赛程=0 场，空档闸门 C-20261006-022）" | Out-File -FilePath $logFile -Append -Encoding utf8
    $script:stepResults += [pscustomobject]@{ Step = "0a-3_赛前特征重算"; ExitCode = "SKIP-0" }
    $script:chainStatus.steps += [pscustomobject]@{ step = "0a-3_赛前特征重算"; exit_code = "SKIP-0" }
    Write-ChainStatus
} else {
    "[$(Get-Date -Format 'HH:mm:ss')] 0a-3 闸门：未来3天赛程=$upcomingN 场（查询失败视为放行），执行重算" | Out-File -FilePath $logFile -Append -Encoding utf8
    Invoke-Step -Name "0a-3_赛前特征重算" -Arguments @("features\sofascore_pre_match_features.py", "--n-recent", "5")
}

# 0a-4. C-20261014-081: Understat xG 补采（--resume 增量，非阻塞：Invoke-Step 失败不中断整链）
# 背景：该采集器 2026-09-22 起断供三周（未挂链无人发现），赛后归因 xG 维度靠 xgscore 兜底降级；
# 与 M0-3 TG 断供同族（"既有数据源断供" bugfix 轨）。赛后 xG/射门/球员 xG 体系三层增量落库。
Invoke-Step -Name "0a-4_understat补采" -Arguments @("collection\final_understat_collector.py", "--leagues", "all", "--season", "26/27", "--resume")

# 0a-5. C-20261016: 竞彩赛程采集挂链（matches 表 + wdl_history 快照；赛程/赛果双接口按日期路由）
# 背景：该采集器此前未挂链，matches 表 2026-10-13 后断更，10-17 赛程靠手工补跑才入库；
# 与 0a-4 understat 断供同族（"既有数据源断供" bugfix 轨）。滚动窗口 today~today+3，
# 未来日期走 getMatchListV1 赛程接口（在售场次+即时赔率），历史日期走赛果接口。
$scheduleEnd = (Get-Date).AddDays(3).ToString("yyyy-MM-dd")
Invoke-Step -Name "0a-5_竞彩赛程采集" -Arguments @("scripts\sporttery_collector.py", "--season", "2026-2027", "--start-date", $reportDate, "--end-date", $scheduleEnd)

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

# 2c. C-20261016-108: λ 队级滚动监控（E3 起算、双维度分路由；E3 n<10 静默积累、脚本异常 exit 0 不阻断）
Invoke-Step -Name "2c_lambda滚动监控" -Arguments @("scripts\lambda_rolling_monitor.py")

# 2d. C-20261016-111: 键错位回退计数器汇总（只读仪表；L3>0 时脚本自带警告文案，非阻断）
Invoke-Step -Name "2d_回退计数器汇总" -Arguments @("scripts\fallback_counter_summary.py")

# 2e. C-20261016-113: 窗口B(P2/P4)就绪检查（只读；满足 G1+G2 才亮灯提醒，永不自动执行迁移）
Invoke-Step -Name "2e_窗口B就绪检查" -Arguments @("scripts\window_b_readiness_check.py")

# 3. 步骤结果汇总 + 失败传播（C-20260926-100 / C-20261017-126）：
#    致命白名单任一步非 0（SKIP-* 视同成功）→ exit 1；观察类步骤（0a-4/0a-5/2c/2d/2e）不传播
"`n===== 步骤执行结果 =====" | Out-File -FilePath $logFile -Append -Encoding utf8
($script:stepResults | Format-Table -AutoSize | Out-String) | Out-File -FilePath $logFile -Append -Encoding utf8
$fatalSteps = @(
    '0a_赛前数据补采', '0a-1_赛前官方首发伤停采集', '0a-2_伤停同步入库',
    '0a-3_赛前特征重算', '0b_竞彩时序赔率回踩', '0c_Elo快照重建', '1_生成预测报告'
)
$fatalFail = $script:stepResults | Where-Object {
    ($fatalSteps -contains $_.Step) -and ("$($_.ExitCode)" -ne '0')
}
$finalCode = if ($fatalFail) { 1 } else { 0 }
if ($fatalFail) {
    foreach ($f in $fatalFail) {
        "[FAIL] 致命步骤失败: $($f.Step) (exit=$($f.ExitCode))，任务标记为失败" | Out-File -FilePath $logFile -Append -Encoding utf8
    }
}

# C-20261017-126: 收尾状态文件 + best-effort 告警（全隔离，告警器异常绝不影响链退出码）
$script:chainStatus.finished_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
$script:chainStatus.exit_code = $finalCode
Write-ChainStatus
try {
    & $py -u (Join-Path $root "scripts\chain_alert.py") inspect-chain --result $statusFile *>&1 |
        Out-File -FilePath $logFile -Append -Encoding utf8
} catch {
    "[告警器调用异常，已隔离] $_" | Out-File -FilePath $logFile -Append -Encoding utf8
}
exit $finalCode