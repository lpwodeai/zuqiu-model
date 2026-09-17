<#
.SYNOPSIS
    五大联赛足球预测模型 - 新对话窗口启动验证脚本
.DESCRIPTION
    基于 docs/CONVERSATION_WORKFLOW_GUIDE.md 第三章流程，自动化执行：
    - 阶段1：模型解析与验证（只读，默认执行）
    - 阶段2：安全启动服务（可选，使用 -Start 参数）
    - 阶段2：服务验证（可选，使用 -Check 参数）

    使用方法：
    1. 完整验证（默认）: .\start_session.ps1
    2. 验证并启动服务:   .\start_session.ps1 -Start
    3. 仅快速检查:       .\start_session.ps1 -Quick
    4. 验证运行中的服务: .\start_session.ps1 -Check

.NOTES
    文件版本: v1.0
    创建时间: 2026-08-06
    关联文档: docs/CONVERSATION_WORKFLOW_GUIDE.md
#>

param(
    [switch]$Start,     # 阶段2.1: 启动服务（如未运行）
    [switch]$Check,     # 阶段2.2: 验证运行中的服务
    [switch]$Quick,     # 快速模式：仅检查关键项
    [switch]$Help       # 显示帮助
)

# ============================================================================
# 0. 初始化配置
# ============================================================================

$ErrorActionPreference = "Continue"
$ProjectRoot = $PSScriptRoot
$DocsPath = Join-Path $ProjectRoot "docs"
$AssetsPath = Join-Path $ProjectRoot "assets"

# 设置控制台编码为 UTF-8（支持中文输出）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 验证结果统计
$script:PassCount = 0
$script:WarnCount = 0
$script:FailCount = 0
$script:Issues = @()

# ============================================================================
# 辅助函数
# ============================================================================

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Cyan
}

function Write-Pass {
    param([string]$Message)
    Write-Host "  [PASS] $Message" -ForegroundColor Green
    $script:PassCount++
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [WARN] $Message" -ForegroundColor Yellow
    $script:WarnCount++
    $script:Issues += "[WARN] $Message"
}

function Write-Fail {
    param([string]$Message)
    Write-Host "  [FAIL] $Message" -ForegroundColor Red
    $script:FailCount++
    $script:Issues += "[FAIL] $Message"
}

function Write-Info {
    param([string]$Message)
    Write-Host "  [INFO] $Message" -ForegroundColor Gray
}

function Get-FileContent {
    param([string]$FilePath)
    if (Test-Path $FilePath) {
        return Get-Content $FilePath -Raw -Encoding UTF8
    }
    return $null
}

function Test-FileExists {
    param([string]$FilePath, [string]$Description)
    if (Test-Path $FilePath) {
        $size = (Get-Item $FilePath).Length
        $sizeKB = [math]::Round($size / 1KB, 1)
        if ($size -eq 0) {
            Write-Warn "$Description 存在但为空: $FilePath"
            return $false
        } else {
            Write-Pass "$Description ($sizeKB KB)"
            return $true
        }
    } else {
        Write-Fail "$Description 不存在: $FilePath"
        return $false
    }
}

# ============================================================================
# 阶段1：模型解析与验证（严格只读）
# ============================================================================

function Invoke-Stage1-Validation {
    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Magenta
    Write-Host "  阶段1：模型解析与验证（严格只读）" -ForegroundColor Magenta
    Write-Host "  规则: 绝对不修改/创建/删除任何文件" -ForegroundColor DarkGray
    Write-Host "========================================================" -ForegroundColor Magenta

    # ----------------------------------------------------------------------
    # 验证项 1: 文档一致性解析
    # ----------------------------------------------------------------------
    Write-Section "验证项 1/5: 文档一致性解析"

    $expectedCV = "50.95%"
    $expectedStage = "阶段七"
    $docsStatus = @{}

    # 1.1 prompt_template.md
    $file = Join-Path $DocsPath "prompt_template.md"
    $content = Get-FileContent $file
    if ($content) {
        $hasCV = $content -match "50\.95%"
        $hasStage = $content -match "阶段七.*服务优化"
        if ($hasCV -and $hasStage) {
            Write-Pass "prompt_template.md: 阶段=七, CV=50.95%"
        } else {
            if (-not $hasCV) { Write-Warn "prompt_template.md: 未找到 CV=50.95%" }
            if (-not $hasStage) { Write-Warn "prompt_template.md: 未找到阶段七标识" }
        }
    } else {
        Write-Fail "prompt_template.md: 文件不存在"
    }

    # 1.2 model_optimization_plan.md
    $file = Join-Path $DocsPath "model_optimization_plan.md"
    $content = Get-FileContent $file
    if ($content) {
        $hasCV = $content -match "50\.95%"
        $hasStage = $content -match "阶段七.*已完成|阶段一至阶段七"
        if ($hasCV -and $hasStage) {
            Write-Pass "model_optimization_plan.md: 阶段=七, CV=50.95%"
        } else {
            if (-not $hasCV) { Write-Warn "model_optimization_plan.md: 未找到 CV=50.95%" }
            if (-not $hasStage) { Write-Warn "model_optimization_plan.md: 未找到阶段七完成标识" }
        }
    } else {
        Write-Fail "model_optimization_plan.md: 文件不存在"
    }

    # 1.3 change_log.md
    $file = Join-Path $DocsPath "change_log.md"
    $content = Get-FileContent $file
    if ($content) {
        $recentEntries = ([regex]::Matches($content, "C-20260806-\d+")).Count
        Write-Pass "change_log.md: 存在 (20260806 系列记录约 $recentEntries 条)"
    } else {
        Write-Fail "change_log.md: 文件不存在"
    }

    # 1.4 key_decisions.md
    $file = Join-Path $DocsPath "key_decisions.md"
    $content = Get-FileContent $file
    if ($content) {
        $decisionCount = ([regex]::Matches($content, "D-\d{8}-\w+")).Count
        Write-Pass "key_decisions.md: 存在 (决策记录约 $decisionCount 条)"
    } else {
        Write-Fail "key_decisions.md: 文件不存在"
    }

    # 1.5 project_memory.md
    $file = Join-Path $DocsPath "project_memory.md"
    $content = Get-FileContent $file
    if ($content) {
        Write-Pass "project_memory.md: 存在"
    } else {
        Write-Fail "project_memory.md: 文件不存在"
    }

    # 1.6 optimization_log.md
    $file = Join-Path $DocsPath "optimization_log.md"
    $content = Get-FileContent $file
    if ($content) {
        $hasStage7 = $content -match "阶段七.*100%"
        if ($hasStage7) {
            Write-Pass "optimization_log.md: 阶段七=100%"
        } else {
            Write-Warn "optimization_log.md: 未找到阶段七 100% 标识"
        }
    } else {
        Write-Fail "optimization_log.md: 文件不存在"
    }

    # 1.7 CONVERSATION_WORKFLOW_GUIDE.md
    $file = Join-Path $DocsPath "CONVERSATION_WORKFLOW_GUIDE.md"
    $content = Get-FileContent $file
    if ($content) {
        $hasV2 = $content -match "v2\.0"
        if ($hasV2) {
            Write-Pass "CONVERSATION_WORKFLOW_GUIDE.md: v2.0 (底层逻辑基础)"
        } else {
            Write-Warn "CONVERSATION_WORKFLOW_GUIDE.md: 版本标识未找到"
        }
    } else {
        Write-Fail "CONVERSATION_WORKFLOW_GUIDE.md: 文件不存在"
    }

    if (-not $Quick) {
        # ----------------------------------------------------------------------
        # 验证项 2: 配置参数验证
        # ----------------------------------------------------------------------
        Write-Section "验证项 2/5: 配置参数验证"

        # .env
        $envFile = Join-Path $ProjectRoot ".env"
        $envContent = Get-FileContent $envFile
        if ($envContent) {
            $nodeEnv = if ($envContent -match "NODE_ENV=(\w+)") { $matches[1] } else { "未设置" }
            $port = if ($envContent -match "PORT=(\d+)") { $matches[1] } else { "未设置" }
            Write-Pass ".env: NODE_ENV=$nodeEnv, PORT=$port"
        } else {
            Write-Warn ".env: 文件不存在（可能使用默认配置）"
        }

        # ecosystem.config.cjs
        $ecoFile = Join-Path $ProjectRoot "ecosystem.config.cjs"
        $ecoContent = Get-FileContent $ecoFile
        if ($ecoContent) {
            $execMode = if ($ecoContent -match "exec_mode:\s*'(\w+)'") { $matches[1] } else { "未设置" }
            $instances = if ($ecoContent -match "instances:\s*(\d+)") { $matches[1] } else { "未设置" }
            $maxMem = if ($ecoContent -match "max_memory_restart:\s*'([^']+)'") { $matches[1] } else { "未设置" }
            Write-Pass "ecosystem.config.cjs: exec_mode=$execMode, instances=$instances, max_mem=$maxMem"
        } else {
            Write-Fail "ecosystem.config.cjs: 文件不存在"
        }

        # model_config.json
        $configFile = Join-Path $AssetsPath "model_config.json"
        $configContent = Get-FileContent $configFile
        if ($configContent) {
            Write-Pass "model_config.json: 存在"
        } else {
            Write-Warn "model_config.json: 文件不存在"
        }
    }

    # ----------------------------------------------------------------------
    # 验证项 3: 模型资产完整性
    # ----------------------------------------------------------------------
    Write-Section "验证项 3/5: 模型资产完整性"

    $assets = @(
        @{ Path = "xgb_model_export.js";       Desc = "XGBoost 模型" },
        @{ Path = "lgb_model_export.js";       Desc = "LightGBM 模型" },
        @{ Path = "feature_scaler_params.js";  Desc = "特征标准化参数" },
        @{ Path = "team_attributes.json";      Desc = "球队属性" },
        @{ Path = "stacking_weights.json";     Desc = "集成权重" },
        @{ Path = "league_tier.json";          Desc = "联赛层级" },
        @{ Path = "league_tier_weight.json";   Desc = "联赛权重" },
        @{ Path = "model_config.json";         Desc = "模型配置" }
    )

    $assetsPass = 0
    foreach ($asset in $assets) {
        $fullPath = Join-Path $AssetsPath $asset.Path
        if (Test-FileExists -FilePath $fullPath -Description $asset.Desc) {
            $assetsPass++
        }
    }
    Write-Info "模型资产: $assetsPass / $($assets.Count) 通过"

    # ----------------------------------------------------------------------
    # 验证项 4: 进程状态查询
    # ----------------------------------------------------------------------
    Write-Section "验证项 4/5: 进程状态查询"

    $pm2Available = $false
    try {
        $pm2Status = pm2 jlist 2>$null | ConvertFrom-Json
        $pm2Available = $true
    } catch {
        $pm2Available = $false
    }

    if ($pm2Available -and $pm2Status) {
        $fiveLeagues = $pm2Status | Where-Object { $_.name -eq "five-leagues" }
        if ($fiveLeagues) {
            $status = $fiveLeagues.pm2_env.status
            $pid = $fiveLeagues.pid
            $uptime = if ($fiveLeagues.pm2_env.pm_uptime) {
                $uptimeTime = [DateTimeOffset]::FromUnixTimeMilliseconds($fiveLeagues.pm2_env.pm_uptime).LocalDateTime
                $uptimeSpan = (Get-Date) - $uptimeTime
                "$([math]::Floor($uptimeSpan.TotalHours))h $($uptimeSpan.Minutes)m"
            } else { "未知" }
            $memory = if ($fiveLeagues.monit.memory) {
                "$([math]::Round($fiveLeagues.monit.memory / 1MB, 1))MB"
            } else { "未知" }
            $restarts = $fiveLeagues.pm2_env.restart_time

            if ($status -eq "online") {
                Write-Pass "PM2 five-leagues: online (PID=$pid, 运行=$uptime, 内存=$memory, 重启=$restarts)"
            } else {
                Write-Warn "PM2 five-leagues: 状态=$status (非 online)"
            }
        } else {
            Write-Warn "PM2 five-leagues: 进程未找到（服务未启动）"
        }
    } else {
        Write-Warn "PM2: 未安装或未运行（服务状态未知）"
    }

    # ----------------------------------------------------------------------
    # 验证项 5: API 健康验证
    # ----------------------------------------------------------------------
    Write-Section "验证项 5/5: API 健康验证"

    try {
        $response = Invoke-RestMethod -Uri "http://localhost:3000/api/health" -Method GET -TimeoutSec 5 -ErrorAction Stop
        $apiStatus = $response.status
        $dbConnected = $response.database.connected
        $xgbLoaded = $response.models.xgbLoaded
        $lgbLoaded = $response.models.lgbLoaded

        if ($apiStatus -eq "healthy") {
            Write-Pass "API /api/health: status=healthy"
        } else {
            Write-Warn "API /api/health: status=$apiStatus"
        }

        if ($dbConnected) {
            Write-Pass "数据库连接: connected=true"
        } else {
            Write-Fail "数据库连接: connected=false"
        }

        if ($xgbLoaded) { Write-Pass "XGBoost 模型: 已加载" }
        else { Write-Warn "XGBoost 模型: 未加载" }

        if ($lgbLoaded) { Write-Pass "LightGBM 模型: 已加载" }
        else { Write-Warn "LightGBM 模型: 未加载" }

    } catch {
        $errMsg = $_.Exception.Message
        if ($errMsg -match "无法连接|actively refused|timed out") {
            Write-Warn "API /api/health: 服务未响应（可能未启动）"
        } else {
            Write-Warn "API /api/health: 调用失败 - $errMsg"
        }
    }
}

# ============================================================================
# 阶段1验证报告输出
# ============================================================================

function Show-ValidationReport {
    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Magenta
    Write-Host "  模型状态评估报告" -ForegroundColor Magenta
    Write-Host "========================================================" -ForegroundColor Magenta

    Write-Host ""
    Write-Host "  统计:" -ForegroundColor White
    Write-Host "    PASS: $script:PassCount" -ForegroundColor Green
    Write-Host "    WARN: $script:WarnCount" -ForegroundColor Yellow
    Write-Host "    FAIL: $script:FailCount" -ForegroundColor Red

    Write-Host ""
    if ($script:FailCount -eq 0 -and $script:WarnCount -eq 0) {
        Write-Host "  [结论] 状态完全一致，可以安全执行后续操作" -ForegroundColor Green
        Write-Host "  风险等级: 低" -ForegroundColor Green
    } elseif ($script:FailCount -eq 0) {
        Write-Host "  [结论] 基本正常，存在 $script:WarnCount 个警告项" -ForegroundColor Yellow
        Write-Host "  风险等级: 中（建议检查警告项）" -ForegroundColor Yellow
    } else {
        Write-Host "  [结论] 存在 $script:FailCount 个异常，需要修复后才能操作" -ForegroundColor Red
        Write-Host "  风险等级: 高" -ForegroundColor Red
    }

    if ($script:Issues.Count -gt 0) {
        Write-Host ""
        Write-Host "  问题清单:" -ForegroundColor White
        foreach ($issue in $script:Issues) {
            Write-Host "    $issue" -ForegroundColor Yellow
        }
    }

    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Magenta
}

# ============================================================================
# 阶段2.1: 安全启动服务
# ============================================================================

function Invoke-Stage2-Start {
    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Magenta
    Write-Host "  阶段2：安全启动服务" -ForegroundColor Magenta
    Write-Host "  规则: 仅允许启动服务，不修改配置" -ForegroundColor DarkGray
    Write-Host "========================================================" -ForegroundColor Magenta

    # 检查服务是否已在运行
    try {
        $pm2Status = pm2 jlist 2>$null | ConvertFrom-Json
        $fiveLeagues = $pm2Status | Where-Object { $_.name -eq "five-leagues" }
        if ($fiveLeagues -and $fiveLeagues.pm2_env.status -eq "online") {
            Write-Info "服务已在运行中，无需重复启动"
            Write-Info "如需重启: pm2 reload five-leagues"
            return
        }
    } catch {}

    # 检查端口占用
    Write-Host ""
    Write-Info "检查端口 3000 占用情况..."
    $portInUse = netstat -ano | Select-String ":3000\s+.*LISTENING"
    if ($portInUse) {
        Write-Warn "端口 3000 已被占用:"
        Write-Host "    $portInUse" -ForegroundColor DarkGray
        Write-Info "正在清理旧进程..."
        try {
            pm2 delete five-leagues 2>$null
        } catch {}
        Start-Sleep -Seconds 2
    } else {
        Write-Pass "端口 3000 空闲"
    }

    # 启动服务
    Write-Host ""
    Write-Info "启动生产环境服务..."
    Set-Location $ProjectRoot
    pm2 start ecosystem.config.cjs --env production
    Write-Host ""
    Write-Info "等待服务初始化 (5秒)..."
    Start-Sleep -Seconds 5

    # 健康检查
    Write-Host ""
    Write-Info "执行健康检查..."
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:3000/api/health" -Method GET -TimeoutSec 10
        if ($response.status -eq "healthy") {
            Write-Pass "服务启动成功: status=healthy"
            Write-Pass "数据库: connected=$($response.database.connected)"
            Write-Pass "XGBoost: loaded=$($response.models.xgbLoaded)"
            Write-Pass "LightGBM: loaded=$($response.models.lgbLoaded)"
        } else {
            Write-Warn "服务已启动但状态异常: $($response.status)"
        }
    } catch {
        Write-Warn "健康检查失败，服务可能仍在初始化中"
        Write-Info "请稍后手动检查: curl http://localhost:3000/api/health"
    }

    # 显示 PM2 状态
    Write-Host ""
    Write-Info "当前 PM2 进程状态:"
    pm2 status
}

# ============================================================================
# 阶段2.2: 服务验证（只读 API 调用）
# ============================================================================

function Invoke-Stage2-Check {
    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Magenta
    Write-Host "  阶段2：服务验证（只读 API 调用）" -ForegroundColor Magenta
    Write-Host "========================================================" -ForegroundColor Magenta

    # 健康检查
    Write-Section "API 1: GET /api/health"
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:3000/api/health" -Method GET -TimeoutSec 5
        Write-Host ($response | ConvertTo-Json -Depth 3) -ForegroundColor DarkGray
        Write-Pass "健康检查通过"
    } catch {
        Write-Fail "健康检查失败: $($_.Exception.Message)"
        return
    }

    # 数据库统计
    Write-Section "API 2: GET /api/db/stats"
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:3000/api/db/stats" -Method GET -TimeoutSec 5
        Write-Host ($response | ConvertTo-Json -Depth 3) -ForegroundColor DarkGray
        Write-Pass "数据库统计获取成功"
    } catch {
        Write-Warn "数据库统计获取失败: $($_.Exception.Message)"
    }

    # 模型热更新状态
    Write-Section "API 3: GET /api/model/reload/status"
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:3000/api/model/reload/status" -Method GET -TimeoutSec 5
        Write-Host ($response | ConvertTo-Json -Depth 3) -ForegroundColor DarkGray
        Write-Pass "热更新状态获取成功"
    } catch {
        Write-Warn "热更新状态获取失败: $($_.Exception.Message)"
    }

    # 预测测试
    Write-Section "API 4: POST /api/predict (阿森纳 vs 利物浦)"
    try {
        $body = @{ homeTeam = "阿森纳"; awayTeam = "利物浦" } | ConvertTo-Json
        $response = Invoke-RestMethod -Uri "http://localhost:3000/api/predict" -Method POST -Body $body -ContentType "application/json" -TimeoutSec 10
        Write-Host ($response | ConvertTo-Json -Depth 5) -ForegroundColor DarkGray
        Write-Pass "预测测试通过"
    } catch {
        Write-Warn "预测测试失败: $($_.Exception.Message)"
    }
}

# ============================================================================
# 帮助信息
# ============================================================================

function Show-Help {
    Write-Host ""
    Write-Host "五大联赛足球预测模型 - 新对话窗口启动验证脚本" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "用法:"
    Write-Host "  .\start_session.ps1              # 完整验证（阶段1，只读）"
    Write-Host "  .\start_session.ps1 -Start        # 验证 + 启动服务（阶段1 + 阶段2.1）"
    Write-Host "  .\start_session.ps1 -Check        # 验证运行中的服务（阶段1 + 阶段2.2）"
    Write-Host "  .\start_session.ps1 -Quick        # 快速模式（仅关键项）"
    Write-Host "  .\start_session.ps1 -Help         # 显示此帮助"
    Write-Host ""
    Write-Host "流程说明（基于 CONVERSATION_WORKFLOW_GUIDE.md 第三章）:"
    Write-Host "  阶段1: 模型解析与验证（只读，默认执行）"
    Write-Host "    - 文档一致性检查（7个文档）"
    Write-Host "    - 配置参数验证"
    Write-Host "    - 模型资产完整性（8个文件）"
    Write-Host "    - PM2 进程状态"
    Write-Host "    - API 健康检查"
    Write-Host ""
    Write-Host "  阶段2.1: 安全启动服务（-Start 参数）"
    Write-Host "    - 检查端口占用"
    Write-Host "    - 启动 PM2 生产环境"
    Write-Host "    - 健康检查验证"
    Write-Host ""
    Write-Host "  阶段2.2: 服务验证（-Check 参数）"
    Write-Host "    - GET /api/health"
    Write-Host "    - GET /api/db/stats"
    Write-Host "    - GET /api/model/reload/status"
    Write-Host "    - POST /api/predict（预测测试）"
    Write-Host ""
    Write-Host "示例:"
    Write-Host "  # 新对话首次启动，验证+启动"
    Write-Host "  .\start_session.ps1 -Start"
    Write-Host ""
    Write-Host "  # 新对话，服务已在运行，仅验证"
    Write-Host "  .\start_session.ps1 -Check"
    Write-Host ""
}

# ============================================================================
# 主流程
# ============================================================================

Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  五大联赛足球预测模型 v8.0" -ForegroundColor Cyan
Write-Host "  新对话窗口启动验证脚本" -ForegroundColor Cyan
Write-Host "  基于 CONVERSATION_WORKFLOW_GUIDE.md v2.0" -ForegroundColor DarkGray
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Info "项目路径: $ProjectRoot"
Write-Info "执行时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Info "执行模式: $(if ($Quick) { '快速' } else { '完整' })"

# 检查项目路径是否存在
if (-not (Test-Path $ProjectRoot)) {
    Write-Fail "项目路径不存在: $ProjectRoot"
    Write-Host ""
    exit 1
}

# 执行阶段1
Invoke-Stage1-Validation

# 输出验证报告
Show-ValidationReport

# 根据参数执行阶段2
if ($Start) {
    Invoke-Stage2-Start
} elseif ($Check) {
    Invoke-Stage2-Check
}

# 最终建议
Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  后续操作建议" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

if ($script:FailCount -gt 0) {
    Write-Host "  [需要修复] 存在 $script:FailCount 个异常项" -ForegroundColor Red
    Write-Host "  建议:" -ForegroundColor White
    Write-Host "    1. 检查异常项详情（见上方问题清单）"
    Write-Host "    2. 参考 docs/change_log.md 查看最近变更"
    Write-Host "    3. 修复后重新运行: .\start_session.ps1"
} elseif ($script:WarnCount -gt 0) {
    Write-Host "  [建议检查] 存在 $script:WarnCount 个警告项" -ForegroundColor Yellow
    Write-Host "  建议:" -ForegroundColor White
    if (-not $Start -and -not $Check) {
        $serviceDown = $script:Issues | Where-Object { $_ -match "未启动|未响应|未找到" }
        if ($serviceDown) {
            Write-Host "    服务似乎未运行，可执行: .\start_session.ps1 -Start"
        }
    }
    Write-Host "    查看日志: pm2 logs five-leagues --lines 50"
} else {
    Write-Host "  [状态正常] 所有验证项通过" -ForegroundColor Green
    Write-Host "  建议:" -ForegroundColor White
    Write-Host "    1. 如需操作模型，请提供明确指令"
    Write-Host "    2. 查看实时日志: pm2 logs five-leagues"
    Write-Host "    3. 性能监控: pm2 monit"
}

Write-Host ""
Write-Host "  常用命令:" -ForegroundColor White
Write-Host "    pm2 status              # 查看服务状态"
Write-Host "    pm2 logs five-leagues   # 查看日志"
Write-Host "    pm2 reload five-leagues # 零停机重启"
Write-Host "    pm2 stop five-leagues   # 停止服务"
Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
