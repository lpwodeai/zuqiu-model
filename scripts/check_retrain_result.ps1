# -*- coding: utf-8 -*-
# 重训结果自动检查（只读）—— C-20261006-023
# 权威状态源：odds.db.training_runs（编排器写入；trigger_state.json 是已退役 T005v3 线，仅旁证）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File check_retrain_result.ps1
$ErrorActionPreference = 'Continue'
$root = 'F:\zuqiu\五大联赛专属模型\五大联赛专属模型'
$db   = Join-Path $root 'data\odds.db'
$log  = Join-Path $root 'logs\training_orchestrator.log'
$now = Get-Date
$today = $now.ToString('yyyy-MM-dd')
$tag = $now.ToString('yyyyMMdd_HHmmss')
$report = Join-Path $root "logs\retrain_check_$($now.ToString('yyyyMMdd')).md"

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# 重训检查 $($now.ToString('yyyy-MM-dd HH:mm:ss'))")
$lines.Add('')

# ---------- A. training_runs 权威状态（DB 路径经环境变量传入，避开 stdin 管道中文编码） ----------
$env:RETRAIN_DB = $db
$env:RETRAIN_DATE = $today
$py = @'
import os, sqlite3, json
c = sqlite3.connect(os.environ["RETRAIN_DB"])
c.row_factory = sqlite3.Row
today = os.environ["RETRAIN_DATE"]
try:
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM training_runs ORDER BY run_id DESC LIMIT 3")]
except sqlite3.OperationalError:
    rows = []
today_runs = [r for r in rows if (r.get('started_at') or '').startswith(today)]
print(json.dumps({"rows": rows, "today_runs": today_runs},
                 ensure_ascii=True, default=str))
'@
$raw = ($py | & C:\Python314\python.exe -u - 2>$null | Select-Object -Last 1)
$status = 'UNKNOWN'
try {
    $j = $raw | ConvertFrom-Json
    $lines.Add('## A. training_runs（权威状态，最近 3 行）')
    $lines.Add('')
    if ($null -eq $j.rows -or @($j.rows).Count -eq 0) {
        $lines.Add('> 表为空（0 行）：编排器历史上从未放行过训练（闸门跳过/进程崩溃均不登记 run 行）')
    } else {
        $lines.Add('| run_id | trigger | type | started_at | finished_at | status | metrics | error |')
        $lines.Add('|---|---|---|---|---|---|---|---|')
        foreach ($r in $j.rows) {
            $metrics = if ($r.metrics) { ($r.metrics -replace "`r?`n", ' ').Substring(0, [Math]::Min(60, $r.metrics.Length)) } else { '' }
            $err = if ($r.error_msg) { ($r.error_msg -replace "`r?`n", ' ').Substring(0, [Math]::Min(80, $r.error_msg.Length)) } else { '' }
            $lines.Add("| $($r.run_id) | $($r.trigger_source) | $($r.training_type) | $($r.started_at) | $($r.finished_at) | **$($r.status)** | $metrics | $err |")
        }
    }
    $lines.Add('')
    $todayCount = if ($null -eq $j.today_runs) { 0 } else { @($j.today_runs).Count }
    if ($todayCount -eq 0) {
        $status = 'NOT_TRIGGERED'
        $lines.Add('> ⚠️ 今日无 training_runs 记录（未放行/未触发/被资源闸门跳过）')
    } else {
        $last = $j.today_runs[0]
        switch ($last.status) {
            'success' {
                $status = 'SUCCESS'
                $lines.Add("> ✅ 今日重训 **success**（run_id=$($last.run_id), type=$($last.training_type), finished=$($last.finished_at)）")
            }
            'running' {
                $status = 'RUNNING'
                $lines.Add("> ⏳ 今日重训仍在 **running**（run_id=$($last.run_id), started=$($last.started_at)）")
            }
            'failed' {
                $status = 'FAILED'
                $lines.Add("> ❌ 今日重训 **failed**（run_id=$($last.run_id)）：$($last.error_msg)")
            }
            default { $status = $last.status }
        }
    }
    $lines.Add('')
} catch {
    $lines.Add("> ⚠️ training_runs 查询失败：$($_.Exception.Message) | raw=$raw")
    $lines.Add('')
}

# ---------- B. orchestrator 日志：今日条目（日志为 UTF-8，须显式指定编码） ----------
$lines.Add('## B. orchestrator 日志（今日关键行）')
$lines.Add('')
$lines.Add('```')
if (Test-Path $log) {
    $todayLogs = Get-Content $log -Encoding UTF8 | Where-Object { $_ -match [regex]::Escape($today.Substring(5)) }
    $tail = $todayLogs | Select-Object -Last 25
    if ($tail) { $lines.AddRange([string[]]$tail) } else { $lines.Add('（今日无日志行）') }
} else { $lines.Add('（日志文件不存在）') }
$lines.Add('```')
$lines.Add('')

# ---------- C. Platt calibration JSON ----------
$lines.Add('## C. Platt calibration JSON（assets/）')
$lines.Add('')
foreach ($m in 'xgb', 'lgb') {
    $p = Join-Path $root "assets\$($m)_calibration.json"
    if (Test-Path $p) {
        $f = Get-Item $p
        $fresh = if ($f.LastWriteTime.ToString('yyyy-MM-dd') -eq $today) { ' 🆕今日落地' } else { '' }
        $lines.Add("- ✅ ``$m`_calibration.json`` 存在 | mtime=$($f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')) | $($f.Length)B$fresh")
    } else {
        $lines.Add("- ❌ ``$m`_calibration.json`` 不存在（生产回退不校准）")
    }
}
$lines.Add('')

# ---------- D. assets 今日刷新文件 ----------
$lines.Add('## D. assets 今日刷新文件 Top 10')
$lines.Add('')
$freshFiles = Get-ChildItem (Join-Path $root 'assets') -File |
    Where-Object { $_.LastWriteTime.ToString('yyyy-MM-dd') -eq $today } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 10
if ($freshFiles) {
    $lines.Add('| mtime | file | size |')
    $lines.Add('|---|---|---|')
    foreach ($f in $freshFiles) {
        $lines.Add("| $($f.LastWriteTime.ToString('HH:mm:ss')) | $($f.Name) | $($f.Length) |")
    }
} else {
    $lines.Add('（assets 今日无文件刷新——训练大概率未执行到落盘阶段）')
}
$lines.Add('')

# ---------- E. 训练进程存活 ----------
$lines.Add('## E. train_models 进程')
$lines.Add('')
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'train_models\.py' }
if ($procs) {
    foreach ($p in $procs) {
        $gp = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        $lines.Add("- ⏳ pid=$($p.ProcessId) 存活 CPU=$([math]::Round($gp.CPU / 60, 1))min started=$($gp.StartTime)")
    }
    if ($status -eq 'NOT_TRIGGERED') { $status = 'RUNNING' }
} else {
    $lines.Add('- 无 train_models.py 进程存活')
}
$lines.Add('')

# ---------- F. 旁证：trigger_state.json（T005v3 退役线，勿据此判定） ----------
$lines.Add('## F. trigger_state.json（旁证，T005v3 退役线）')
$lines.Add('')
$ts = Join-Path $root 'deployment\trigger_state.json'
if (Test-Path $ts) { $lines.Add('```'); $lines.Add((Get-Content $ts -Raw -Encoding UTF8).Trim()); $lines.Add('```') }
$lines.Add('')

# ---------- 总结论 ----------
$verdict = switch ($status) {
    'SUCCESS'       { '✅ SUCCESS：重训成功——核对 C 节 calibration JSON 是否今日落地、D 节资产刷新' }
    'FAILED'        { '❌ FAILED：重训失败——看 A 节 error_msg 与 B 节日志尾部' }
    'RUNNING'       { '⏳ RUNNING：仍在训练中（全量 timeout=120min），等待下一检查点 06:45' }
    'NOT_TRIGGERED' { '⚠️ NOT_TRIGGERED：04:30 未产生 run——大概率 MEM>92 被闸门跳过，看 B 节' }
    default         { "⚠️ UNKNOWN：$status" }
}
$lines.Add("## 结论：$verdict")
$lines.Add('')

$header = "`n<!-- check-tag=$tag status=$status -->`n"
($header + ($lines -join "`r`n") + "`r`n") | Out-File -FilePath $report -Append -Encoding utf8
Write-Output "[$tag] status=$status report=$report"
