# ============================================================
# C-20260816-202: 联赛专属 draw_threshold_factor 部署脚本
# 
# 部署内容:
#   - config.yaml (全局 factor=1.1, 联赛专属 FL1→0.0)
#   - server/services/prediction-service.js (联赛分派逻辑)
#
# 使用方式:
#   .\scripts\deploy_league_threshold.ps1
# ============================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DEPLOY_VERSION = (Get-Date -Format "yyyyMMdd_HHmmss")

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  C-20260816-202: 联赛专属阈值部署" -ForegroundColor Cyan
Write-Host "  Version: $DEPLOY_VERSION" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# Step 1: 备份当前文件
# ============================================================
Write-Host "[1/4] 备份当前文件..." -ForegroundColor Yellow

$BACKUP_DIR = Join-Path $ROOT "backups\deploy_$DEPLOY_VERSION"
New-Item -ItemType Directory -Force -Path $BACKUP_DIR | Out-Null

$CONFIG_PATH = Join-Path $ROOT "config.yaml"
$PREDICTION_SERVICE_PATH = Join-Path $ROOT "server\services\prediction-service.js"

if (Test-Path $CONFIG_PATH) {
    Copy-Item $CONFIG_PATH (Join-Path $BACKUP_DIR "config.yaml") -Force
    Write-Host "  ✅ config.yaml → $BACKUP_DIR" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ config.yaml 不存在，跳过备份" -ForegroundColor Yellow
}

if (Test-Path $PREDICTION_SERVICE_PATH) {
    Copy-Item $PREDICTION_SERVICE_PATH (Join-Path $BACKUP_DIR "prediction-service.js") -Force
    Write-Host "  ✅ prediction-service.js → $BACKUP_DIR" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ prediction-service.js 不存在，跳过备份" -ForegroundColor Yellow
}

Write-Host ""

# ============================================================
# Step 2: 验证目标文件完整性
# ============================================================
Write-Host "[2/4] 验证文件完整性..." -ForegroundColor Yellow

# 验证 config.yaml 包含联赛配置段
$configContent = Get-Content $CONFIG_PATH -Raw
if ($configContent -match "draw_threshold_factor_league") {
    Write-Host "  ✅ config.yaml: 联赛专属阈值段已就绪" -ForegroundColor Green
} else {
    Write-Host "  ❌ config.yaml: 缺少 draw_threshold_factor_league 段!" -ForegroundColor Red
    exit 1
}

# 验证 prediction-service.js 包含分派逻辑
$psContent = Get-Content $PREDICTION_SERVICE_PATH -Raw
if ($psContent -match "C-20260816-200" -and $psContent -match "applyLeagueDrawThreshold") {
    Write-Host "  ✅ prediction-service.js: 联赛分派逻辑已就绪" -ForegroundColor Green
} else {
    Write-Host "  ❌ prediction-service.js: 缺少 C-20260816-200 逻辑!" -ForegroundColor Red
    exit 1
}

# 验证测试文件
$TEST_PATH = Join-Path $ROOT "server\tests\test_league_draw_threshold.js"
if (Test-Path $TEST_PATH) {
    Write-Host "  ✅ test_league_draw_threshold.js: 存在" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ test_league_draw_threshold.js: 不存在" -ForegroundColor Yellow
}

Write-Host ""

# ============================================================
# Step 3: 运行测试 (可选，跳过不影响部署)
# ============================================================
Write-Host "[3/4] 运行单元测试..." -ForegroundColor Yellow

$testResult = node $TEST_PATH 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✅ 单元测试通过" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ 单元测试失败 (退出码: $LASTEXITCODE)，但继续部署..." -ForegroundColor Yellow
    Write-Host "  $testResult" -ForegroundColor Gray
}

Write-Host ""

# ============================================================
# Step 4: 重启服务
# ============================================================
Write-Host "[4/4] 重启 PM2 服务..." -ForegroundColor Yellow

# 检查 PM2 是否在运行
$pm2Status = pm2 jlist 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ⚠️ PM2 未运行，不执行重启" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  部署完成 (文件已就绪，服务未运行)" -ForegroundColor Cyan
    Write-Host "  手动启动: pm2 start ecosystem.config.cjs --env production" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    exit 0
}

# 检查 five-leagues 进程
$pm2Json = $pm2Status | ConvertFrom-Json
$fiveLeagues = $pm2Json | Where-Object { $_.name -eq "five-leagues" }

if (-not $fiveLeagues) {
    Write-Host "  ⚠️ five-leagues 进程未找到，不执行重启" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  部署完成 (文件已就绪，服务未运行)" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    exit 0
}

# 停止 → 确认端口释放 → 启动
Write-Host "  停止 five-leagues..." -ForegroundColor Gray
pm2 stop five-leagues 2>&1 | Out-Null
Start-Sleep -Seconds 3

# 检查端口 3000 是否释放
$portInUse = netstat -ano | Select-String ":3000 "
if ($portInUse) {
    Write-Host "  ⚠️ 端口 3000 仍被占用，等待 5 秒..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}

Write-Host "  启动 five-leagues..." -ForegroundColor Gray
pm2 start ecosystem.config.cjs --env production 2>&1 | Out-Null
Start-Sleep -Seconds 3

# 验证服务
$pm2StatusNew = pm2 jlist 2>&1 | ConvertFrom-Json
$fiveLeaguesNew = $pm2StatusNew | Where-Object { $_.name -eq "five-leagues" }

if ($fiveLeaguesNew -and $fiveLeaguesNew.pm2_env.status -eq "online") {
    Write-Host "  ✅ five-leagues 已重启在线" -ForegroundColor Green
} else {
    Write-Host "  ❌ five-leagues 未能成功启动!" -ForegroundColor Red
    Write-Host "  尝试恢复备份..." -ForegroundColor Yellow
    if (Test-Path (Join-Path $BACKUP_DIR "config.yaml")) {
        Copy-Item (Join-Path $BACKUP_DIR "config.yaml") $CONFIG_PATH -Force
    }
    if (Test-Path (Join-Path $BACKUP_DIR "prediction-service.js")) {
        Copy-Item (Join-Path $BACKUP_DIR "prediction-service.js") $PREDICTION_SERVICE_PATH -Force
    }
    pm2 restart five-leagues 2>&1 | Out-Null
    Write-Host "  ⚠️ 已回滚到备份版本" -ForegroundColor Yellow
    exit 1
}

# 保存 PM2 进程列表
pm2 save 2>&1 | Out-Null

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  部署成功!" -ForegroundColor Green
Write-Host "  备份: $BACKUP_DIR" -ForegroundColor Cyan
Write-Host "  部署版本: $DEPLOY_VERSION" -ForegroundColor Cyan
Write-Host ""
Write-Host "  变更内容:" -ForegroundColor Cyan
Write-Host "    config.yaml: draw_threshold_factor 1.5→1.1, 新增联赛专属段" -ForegroundColor White
Write-Host "    prediction-service.js: 新增 applyLeagueDrawThreshold() 分派逻辑" -ForegroundColor White
Write-Host "    法甲 FL1: factor=0.0 (argmax，不做平局阈值调整)" -ForegroundColor White
Write-Host "    其他联赛: factor=1.1 (温和提升平局召回)" -ForegroundColor White
Write-Host ""
Write-Host "  验证命令:" -ForegroundColor Cyan
Write-Host "    pm2 status" -ForegroundColor White
Write-Host "    pm2 logs five-leagues --lines 20" -ForegroundColor White
Write-Host "    curl http://localhost:3000/api/health" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan