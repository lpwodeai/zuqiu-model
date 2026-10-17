# setup_chain_watchdog.ps1 — C-20261017-126
# 注册赛前链看门狗计划任务：每日 23:55（evening 首巡）+ 01:10（late 超时复判）双触发。
# 设置与 TraeCode_PrematchReport_2030 对齐：InteractiveToken（桌面气泡可弹）、
# 电源不限制、IgnoreNew、StartWhenAvailable（关机错过开机补跑）。
# 回滚：Unregister-ScheduledTask -TaskName 'TraeCode_ChainWatchdog' -Confirm:$false
$ErrorActionPreference = 'Stop'
$root = 'f:\zuqiu\五大联赛专属模型\五大联赛专属模型'
$py = 'C:\Python314\python.exe'
$taskName = 'TraeCode_ChainWatchdog'

$action = New-ScheduledTaskAction -Execute $py -Argument '-u scripts\chain_watchdog.py' -WorkingDirectory $root

$t1 = New-ScheduledTaskTrigger -Daily -At '23:55'
$t2 = New-ScheduledTaskTrigger -Daily -At '01:10'

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger @($t1, $t2) -Settings $settings -Principal $principal `
    -Description 'C-20261017-126 赛前链看门狗：23:55/01:10 巡检 20:30 链心跳/步骤/产物，失败本地告警（logs/chain_status/）' `
    -Force | Out-Null

Write-Output "[OK] $taskName registered:"
Get-ScheduledTask -TaskName $taskName |
    Select-Object TaskName, State |
    Format-Table -AutoSize
(Get-ScheduledTask -TaskName $taskName).Triggers |
    ForEach-Object { Write-Output ("  trigger daily at {0:hh\:mm}" -f $_.StartBoundary) }
