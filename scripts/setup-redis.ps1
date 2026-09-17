# ============================================================
# Redis 一键安装 & 配置脚本 (Windows)
# 用于足球预测模型缓存服务
# 用法: powershell -ExecutionPolicy Bypass -File scripts/setup-redis.ps1
# ============================================================

param(
    [switch]$SkipDownload,
    [switch]$InstallAsService,
    [string]$RedisPort = "6379",
    [string]$RedisPassword = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ToolsDir = Join-Path $ProjectRoot "tools"
$RedisDir = Join-Path $ToolsDir "redis"
$RedisExe = Join-Path $RedisDir "redis-server.exe"
$RedisCli = Join-Path $RedisDir "redis-cli.exe"

# Redis for Windows 下载地址 (tporadowski/redis, 社区维护最活跃的 Windows 版本)
$RedisVersion = "5.0.14.1"
$RedisZipUrl = "https://github.com/tporadowski/redis/releases/download/v$RedisVersion/Redis-x64-$RedisVersion.zip"
$RedisZip = Join-Path $ToolsDir "redis-x64-$RedisVersion.zip"

Write-Host @"
╔══════════════════════════════════════════════════════════╗
║         Redis 一键安装脚本 - 足球预测模型               ║
║         版本: $RedisVersion | 端口: $RedisPort           ║
╚══════════════════════════════════════════════════════════╝
"@

# ============================================================
# 1. 检查是否已安装
# ============================================================
if (Test-Path $RedisExe) {
    Write-Host "[1/5] Redis 已安装: $RedisExe" -ForegroundColor Green
    $alreadyInstalled = $true
} else {
    Write-Host "[1/5] Redis 未安装，开始下载..." -ForegroundColor Yellow
    $alreadyInstalled = $false
}

# ============================================================
# 2. 下载 Redis (如果需要)
# ============================================================
if (-not $alreadyInstalled -and -not $SkipDownload) {
    Write-Host "[2/5] 下载 Redis $RedisVersion ..." -ForegroundColor Yellow
    
    if (-not (Test-Path $ToolsDir)) {
        New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
    }
    
    try {
        Write-Host "  下载地址: $RedisZipUrl"
        Write-Host "  保存到: $RedisZip"
        Invoke-WebRequest -Uri $RedisZipUrl -OutFile $RedisZip -UseBasicParsing
        Write-Host "  下载完成: $([math]::Round((Get-Item $RedisZip).Length / 1MB, 1)) MB" -ForegroundColor Green
    } catch {
        Write-Host "  GitHub 下载失败，尝试使用备用镜像..." -ForegroundColor Yellow
        $backupUrl = "https://ghproxy.com/https://github.com/tporadowski/redis/releases/download/v$RedisVersion/Redis-x64-$RedisVersion.zip"
        try {
            Invoke-WebRequest -Uri $backupUrl -OutFile $RedisZip -UseBasicParsing
        } catch {
            Write-Host "  下载失败。请手动下载并放置到: $RedisZip" -ForegroundColor Red
            Write-Host "  下载地址: $RedisZipUrl" -ForegroundColor Red
            exit 1
        }
    }
    
    # 解压
    Write-Host "  解压到 $RedisDir ..."
    if (Test-Path $RedisDir) { Remove-Item -Recurse -Force $RedisDir }
    Expand-Archive -Path $RedisZip -DestinationPath $RedisDir -Force
    
    # 清理 zip
    Remove-Item $RedisZip -Force -ErrorAction SilentlyContinue
    Write-Host "  解压完成" -ForegroundColor Green
} elseif ($alreadyInstalled) {
    Write-Host "[2/5] 跳过下载(已安装)" -ForegroundColor Gray
} else {
    Write-Host "[2/5] 跳过下载(--SkipDownload)" -ForegroundColor Gray
}

# ============================================================
# 3. 配置 redis.conf
# ============================================================
Write-Host "[3/5] 配置 Redis..." -ForegroundColor Yellow

$now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$redisDirUnix = $RedisDir -replace '\\', '/'
if ($RedisPassword) {
    $authLine = "requirepass $RedisPassword"
} else {
    $authLine = "# requirepass (no password)"
}

$redisConf = @"
# Redis 配置文件 - 足球预测模型
# 自动生成于 $now

# 网络
bind 127.0.0.1
port $RedisPort
protected-mode yes

# 认证
$authLine

# 持久化 (RDB)
save 900 1
save 300 10
save 60 10000
dbfilename dump.rdb
dir $redisDirUnix

# 日志
loglevel notice
logfile "$redisDirUnix/redis.log"

# 内存
maxmemory 256mb
maxmemory-policy allkeys-lru

# 连接
timeout 300
tcp-keepalive 60
maxclients 100

# 慢查询
slowlog-log-slower-than 10000
slowlog-max-len 128
"@

$confPath = Join-Path $RedisDir "redis.conf"
Set-Content -Path $confPath -Value $redisConf -Encoding UTF8
Write-Host "  配置已写入: $confPath" -ForegroundColor Green

# ============================================================
# 4. 启动 Redis 并测试
# ============================================================
Write-Host "[4/5] 启动 Redis 并测试..." -ForegroundColor Yellow

# 先检查端口是否被占用
$existingProcess = Get-NetTCPConnection -LocalPort $RedisPort -ErrorAction SilentlyContinue
if ($existingProcess -and $existingProcess.State -eq "Listen") {
    Write-Host "  端口 $RedisPort 已被占用，跳过启动" -ForegroundColor Yellow
    Write-Host "  如需重启，请先停止占用进程: taskkill /PID $($existingProcess.OwningProcess) /F" -ForegroundColor Gray
} else {
    # 启动 Redis (后台运行)
    $process = Start-Process -FilePath $RedisExe -ArgumentList "`"$confPath`"" -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    
    # 测试连接
    if ($RedisPassword) {
        $testResult = & $RedisCli -a $RedisPassword -p $RedisPort PING 2>&1
    } else {
        $testResult = & $RedisCli -p $RedisPort PING 2>&1
    }
    
    if ($testResult -eq "PONG") {
        Write-Host "  Redis 启动成功! PING -> PONG" -ForegroundColor Green
    } else {
        Write-Host "  Redis 启动测试失败: $testResult" -ForegroundColor Red
        Write-Host "  请检查: $RedisDir/redis.log" -ForegroundColor Yellow
        # 不退出，继续尝试安装服务
    }
}

# ============================================================
# 5. 安装为 Windows 服务 (可选)
# ============================================================
if ($InstallAsService) {
    Write-Host "[5/5] 安装为 Windows 服务..." -ForegroundColor Yellow
    
    $nssmPath = Join-Path $ToolsDir "nssm.exe"
    if (-not (Test-Path $nssmPath)) {
        Write-Host "  nssm.exe 未找到，下载中..."
        $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
        $nssmZip = Join-Path $ToolsDir "nssm.zip"
        try {
            Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -UseBasicParsing
            Expand-Archive -Path $nssmZip -DestinationPath $ToolsDir -Force
            $nssmExtracted = Get-ChildItem -Path $ToolsDir -Directory -Filter "nssm-*" | Select-Object -First 1
            if ($nssmExtracted) {
                Copy-Item (Join-Path $nssmExtracted.FullName "win64\nssm.exe") $nssmPath -Force
            }
            Remove-Item $nssmZip -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Host "  nssm 下载失败，跳过服务安装。可手动安装" -ForegroundColor Red
        }
    }
    
    if (Test-Path $nssmPath) {
        # 先停止已有服务
        & $nssmPath stop Redis 2>$null | Out-Null
        Start-Sleep -Seconds 2
        
        # 安装服务
        $args = @(
            "install", "Redis",
            $RedisExe,
            "`"$confPath`""
        )
        & $nssmPath @args
        
        & $nssmPath set Redis AppDirectory $RedisDir
        & $nssmPath set Redis DisplayName "Redis Cache (Football Model)"
        & $nssmPath set Redis Description "足球预测模型 Redis 缓存服务"
        & $nssmPath set Redis Start SERVICE_AUTO_START
        & $nssmPath set Redis AppStdout (Join-Path $RedisDir "redis-out.log")
        & $nssmPath set Redis AppStderr (Join-Path $RedisDir "redis-err.log")
        & $nssmPath set Redis AppRotateFiles 1
        & $nssmPath set Redis AppRotateOnline 1
        & $nssmPath set Redis AppRotateSeconds 86400
        & $nssmPath set Redis AppRotateBytes 1048576
        
        # 启动服务
        & $nssmPath start Redis
        
        Write-Host "  Redis Windows 服务已安装并启动" -ForegroundColor Green
        Write-Host "  管理命令:" -ForegroundColor Gray
        Write-Host "    .\tools\nssm.exe status Redis" -ForegroundColor Gray
        Write-Host "    .\tools\nssm.exe restart Redis" -ForegroundColor Gray
        Write-Host "    .\tools\nssm.exe remove Redis confirm" -ForegroundColor Gray
    }
} else {
    Write-Host "[5/5] 跳过服务安装(使用 --InstallAsService 可安装为 Windows 服务)" -ForegroundColor Gray
}

# ============================================================
# 6. 环境变量提示
# ============================================================
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════╗"
Write-Host "║  Redis 安装完成!                                        ║"
Write-Host "╚══════════════════════════════════════════════════════════╝"
Write-Host ""
Write-Host "  默认配置 (无需修改):" -ForegroundColor Cyan
Write-Host "    REDIS_HOST = localhost"
Write-Host "    REDIS_PORT = $RedisPort"
$passDisplay = if ($RedisPassword) { $RedisPassword } else { "(无)" }
Write-Host "    REDIS_PASSWORD = $passDisplay"
Write-Host "    REDIS_DB = 0"
Write-Host ""
Write-Host "  如需自定义，可设置环境变量:" -ForegroundColor Cyan
Write-Host '    set REDIS_HOST=localhost'
Write-Host '    set REDIS_PORT=6379'
Write-Host ""
Write-Host "  手动管理 Redis:" -ForegroundColor Cyan
Write-Host "    启动: $RedisExe `"$confPath`""
Write-Host "    测试: $RedisCli -p $RedisPort PING"
Write-Host "    监控: $RedisCli -p $RedisPort MONITOR"
Write-Host "    日志: $RedisDir\redis.log"
Write-Host ""
Write-Host "  重启预测服务以连接 Redis:" -ForegroundColor Cyan
Write-Host "    .\tools\nssm.exe restart FiveLeagues"
Write-Host ""

# 返回连接状态
if ($RedisCli) {
    Write-Host "  验证 Redis 连接..."
    if ($RedisPassword) {
        $status = & $RedisCli -a $RedisPassword -p $RedisPort PING 2>&1
    } else {
        $status = & $RedisCli -p $RedisPort PING 2>&1
    }
    if ($status -eq "PONG") {
        Write-Host "  Redis 连接正常: PONG" -ForegroundColor Green
    } else {
        Write-Host "  Redis 连接失败，请查看日志: $RedisDir\redis.log" -ForegroundColor Yellow
    }
}