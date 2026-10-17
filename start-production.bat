@echo off
chcp 65001 >nul
title 五大联赛预测模型 - 生产环境部署

REM C-20260929-004：版本号与 package.json 对齐（7.5.0），明确本脚本语义为 Windows 首次部署入口
echo ========================================
echo   五大联赛足球预测模型 v7.5.0 部署脚本
echo   (Windows 首次部署入口；Linux 服务器请用 deploy.sh)
echo   (日常启动由 PM2 任务计划 FiveLeagues_PM2_Startup 自启)
echo ========================================
echo.

REM ===== 1. 检查依赖 =====
echo [1/5] 检查环境依赖...

where node >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [错误] Node.js 未安装，请先安装 Node.js 18+
    echo 下载地址: https://nodejs.org/
    pause
    exit /b 1
)

where pm2 >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [警告] PM2 未安装，正在安装...
    call npm install -g pm2
    if %ERRORLEVEL% neq 0 (
        echo [错误] PM2 安装失败
        pause
        exit /b 1
    )
)

echo [完成] Node.js:
node --version
echo [完成] PM2:
pm2 --version
echo.

REM C-20260929-004：移植 deploy.sh C-20260927-025 R2 修复模式
REM 全量安装（含 devDeps 中的 vite/rollup/terser 构建工具链）→ 本地构建 client/dist → 裁剪 devDeps
REM 原 npm install --production 会跳过 vite/antd/react 等构建工具，server/index.js 生产模式仅托管 client/dist，全新部署后 API 正常但前端 404
REM ===== 2. 安装依赖并构建前端 =====
echo [2/5] 安装项目依赖（含前端构建工具链）...
if not exist "node_modules" (
    call npm install
    if %ERRORLEVEL% neq 0 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
    echo [完成] 依赖安装完成（含 vite/react/antd 构建工具链）
) else (
    echo [跳过] node_modules 已存在
)

REM 构建前端生产产物（fresh 部署必做；产物不入 .gitignore 不随仓库分发，只能在本地构建）
if not exist "client\dist\index.html" (
    if not exist "node_modules\vite" (
        echo [提示] 构建工具缺失（devDeps 已裁剪），重新安装 ...
        call npm install
        if %ERRORLEVEL% neq 0 (
            echo [错误] 构建工具补装失败
            pause
            exit /b 1
        )
    )
    echo [执行] 前端构建 (npm run client:build)...
    call npm run client:build
    if %ERRORLEVEL% neq 0 (
        echo [错误] 前端构建失败 (npm run client:build)
        pause
        exit /b 1
    )
    echo [完成] 前端构建完成: client\dist
) else (
    echo [跳过] client\dist 已存在（更新前端代码后需手动重建: npm install ^&^& npm run client:build ^&^& npm prune --omit=dev）
)

REM 裁剪开发依赖，保持生产运行时精简（构建已完成，devDeps 不再需要）
echo [执行] 裁剪开发依赖 (npm prune --omit=dev)...
call npm prune --omit=dev
if %ERRORLEVEL% neq 0 (
    echo [警告] npm prune 未成功，devDependencies 保留在 node_modules（不影响服务运行）
)
echo.

REM ===== 3. 创建必要目录 =====
echo [3/5] 创建运行时目录...
if not exist "logs" mkdir logs
if not exist "data" mkdir data
if not exist "backups" mkdir backups
echo [完成] 目录准备就绪
echo.

REM ===== 4. 检查配置 =====
echo [4/5] 检查配置文件...
REM C-20260929-004：移植 deploy.sh C-20260927-025 .env 自动生成逻辑到 Windows（无 .env.example 时生成最小 .env + 随机 JWT_SECRET）
if not exist ".env" (
    echo [提示] .env 文件不存在，正在生成最小 .env ...
    REM Windows 用 powershell 生成随机 UUID 作 JWT_SECRET（替代 Linux /proc/sys/kernel/random/uuid）
    for /f "delims=" %%i in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString('N')"') do set GEN_SECRET=%%i
    setlocal enabledelayedexpansion
    (
        echo # 由 start-production.bat 自动生成（最小配置），请按实际环境修改
        echo NODE_ENV=production
        echo PORT=3000
        echo CLIENT_URL=https://your-domain.com
        echo JWT_SECRET=!GEN_SECRET!
    ) > ".env"
    endlocal
    echo [完成] 已生成最小 .env（含随机 JWT_SECRET）
    echo [提示] 请修改 CLIENT_URL 为实际域名
) else (
    echo [完成] .env 配置文件已存在
)
echo.

REM ===== 5. 启动服务 =====
echo [5/5] 启动生产服务...
echo.
echo 选择启动模式:
echo   [1] 生产环境 (production) - 推荐
echo   [2] 开发环境 (development)
echo   [3] 仅测试 (test) - 前台运行，退出即停止
echo.

set /p choice="请选择 [1-3] (默认: 1): "

if "%choice%"=="2" goto :start_dev
if "%choice%"=="3" goto :start_test
goto :start_production

REM C-20260929-003：ecosystem.config.js -> ecosystem.config.cjs（实测 .js 不存在，.cjs 是 PM2 cluster 入口，C-20260812-209）
:start_production
echo.
echo 正在以生产环境模式启动...
call pm2 start ecosystem.config.cjs --env production
goto :done

:start_dev
echo.
echo 正在以开发环境模式启动...
call pm2 start ecosystem.config.cjs --env development
goto :done

:start_test
echo.
echo 正在以前台测试模式启动 (Ctrl+C 停止)...
node server/index.js
goto :eof

:done
echo.
if %ERRORLEVEL% equ 0 (
    echo ========================================
    echo   部署成功！
    echo ========================================
    echo.
    echo   服务地址: http://localhost:3000
    echo   健康检查: http://localhost:3000/api/health
    echo   模型管理: http://localhost:3000/api/model/reload/status
    echo.
    echo   可用命令:
    echo     pm2 status              - 查看服务状态
    echo     pm2 logs five-leagues   - 查看实时日志
    echo     pm2 monit               - 性能监控面板
    echo     pm2 reload five-leagues - 零停机重启
    echo     pm2 stop five-leagues   - 停止服务
    echo.
    echo   注意: 生产环境请配置 Nginx 反向代理和 HTTPS
echo   详见: docs\五大联赛全栈框架设计.md §3.5 部署架构
echo.
    call pm2 status
) else (
    echo.
    echo [错误] 服务启动失败，请查看日志:
    echo   pm2 logs five-leagues --err
)

echo.
pause