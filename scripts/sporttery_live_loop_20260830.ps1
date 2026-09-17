$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
while ($true) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Output "=== [$stamp] start sporttery live collect ==="
    python "scripts\sporttery_live_collector.py"
    Write-Output "=== [$stamp] done, sleep 7200s ==="
    Start-Sleep -Seconds 7200
}