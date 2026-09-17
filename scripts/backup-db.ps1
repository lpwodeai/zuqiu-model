$baseDir = $PSScriptRoot | Split-Path -Parent
$sourcePath = Join-Path $baseDir "data\five_leagues.db"
$backupDir = Join-Path $baseDir "backup"
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupPath = Join-Path $backupDir "five_leagues_$timestamp.db"

if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir | Out-Null
}

if (-not (Test-Path $sourcePath)) {
    Write-Host "ERROR: Source database file not found: $sourcePath"
    exit 1
}

try {
    Copy-Item $sourcePath $backupPath
    $fileSize = (Get-Item $backupPath).Length / 1KB
    Write-Host "SUCCESS: Database backup completed: $backupPath ($($fileSize.ToString('N2')) KB)"
} catch {
    Write-Host "ERROR: Backup failed: $_"
    exit 1
}

Get-ChildItem -Path $backupDir -Filter "five_leagues_*.db" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Force

$remainingBackups = (Get-ChildItem -Path $backupDir -Filter "five_leagues_*.db").Count
Write-Host "SUCCESS: Cleaned up backups older than 7 days, $remainingBackups backups retained"