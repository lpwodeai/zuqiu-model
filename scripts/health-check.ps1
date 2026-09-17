$baseUrl = "http://localhost:3000/api"
$allPassed = $true

Write-Host "========================================"
Write-Host "  Five Leagues Predictor Health Check"
Write-Host "========================================"
Write-Host ""

function Test-Endpoint {
    param($name, $url, $method = "GET", $body = $null)
    
    try {
        if ($method -eq "POST") {
            $response = Invoke-WebRequest -Uri $url -Method POST -ContentType "application/json" -Body $body -UseBasicParsing -ErrorAction Stop
        } else {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -ErrorAction Stop
        }
        Write-Host "  [OK] $name"
        return $true
    } catch {
        Write-Host "  [FAIL] $name - $($_.Exception.Message)"
        return $false
    }
}

Write-Host "1. Server Health Check"
if (-not (Test-Endpoint "Health API" "$baseUrl/health")) { $allPassed = $false }
Write-Host ""

Write-Host "2. Prediction Service"
if (-not (Test-Endpoint "Teams List" "$baseUrl/teams")) { $allPassed = $false }
if (-not (Test-Endpoint "Prediction API" "$baseUrl/predict" "POST" '{"homeTeam":"bl1_bay","awayTeam":"fl1_psg"}')) { $allPassed = $false }
Write-Host ""

Write-Host "3. Cache Service"
if (-not (Test-Endpoint "Cache Stats" "$baseUrl/cache/stats")) { $allPassed = $false }
Write-Host ""

Write-Host "4. Review Service"
if (-not (Test-Endpoint "Review Stats" "$baseUrl/review/stats")) { $allPassed = $false }
if (-not (Test-Endpoint "Model Latest" "$baseUrl/review/model/latest")) { $allPassed = $false }
if (-not (Test-Endpoint "Train Status" "$baseUrl/review/train/status")) { $allPassed = $false }
Write-Host ""

Write-Host "5. Database Check"
$dbPath = Join-Path $PSScriptRoot "..\data\five_leagues.db"
if (Test-Path $dbPath) {
    $size = (Get-Item $dbPath).Length / 1KB
    Write-Host "  [OK] Database file exists ($($size.ToString('N2')) KB)"
} else {
    Write-Host "  [FAIL] Database file not found"
    $allPassed = $false
}
Write-Host ""

Write-Host "6. Backup Check"
$backupDir = Join-Path $PSScriptRoot "..\backup"
if (Test-Path $backupDir) {
    $backups = Get-ChildItem $backupDir -Filter "five_leagues_*.db" | Sort-Object LastWriteTime -Descending
    if ($backups.Count -gt 0) {
        $latest = $backups[0]
        Write-Host "  [OK] $($backups.Count) backup(s) found, latest: $($latest.Name) ($($latest.LastWriteTime))"
    } else {
        Write-Host "  [WARN] No backup files found"
    }
} else {
    Write-Host "  [WARN] Backup directory not found"
}
Write-Host ""

Write-Host "========================================"
if ($allPassed) {
    Write-Host "  All checks PASSED" -ForegroundColor Green
} else {
    Write-Host "  Some checks FAILED" -ForegroundColor Red
}
Write-Host "========================================"

if ($allPassed) { exit 0 } else { exit 1 }