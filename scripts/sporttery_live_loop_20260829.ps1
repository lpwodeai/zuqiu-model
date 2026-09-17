$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# 项目根目录 = 本脚本所在 scripts\ 的上一级（动态定位，不硬编码中文盘符路径）
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$deadline = Get-Date '2026-08-30 03:30:00'
while ((Get-Date) -lt $deadline) {
    python scripts\sporttery_live_collector.py --date 2026-08-29
    Start-Sleep -Seconds 1800
}