@echo off
chcp 65001 >nul
title 五大联赛预测模型 - 生产环境部署

echo ========================================
echo   五大联赛足球预测模型 v8.0 部署脚本
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

REM ===== 2. 安装依赖 =====
echo [2/5] 安装项目依赖...
if not exist "node_modules" (
    call npm install --production
    if %ERRORLEVEL% neq 0 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
) else (
    echo [跳过] node_modules 已存在
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
if not exist ".env" (
    echo [警告] .env 文件不存在，从模板创建...
    if exist ".env.example" (
        copy .env.example .env
        echo [提示] 请编辑 .env 文件配置生产环境参数
    ) else (
        echo [错误] 请手动创建 .env 文件
        pause
        exit /b 1
    )
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

:start_production
echo.
echo 正在以生产环境模式启动...
call pm2 start ecosystem.config.js --env production
goto :done

:start_dev
echo.
echo 正在以开发环境模式启动...
call pm2 start ecosystem.config.js --env development
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
    echo   详见: docs\DEPLOYMENT_GUIDE.md
    echo.
    call pm2 status
) else (
    echo.
    echo [错误] 服务启动失败，请查看日志:
    echo   pm2 logs five-leagues --err
)

echo.
pause