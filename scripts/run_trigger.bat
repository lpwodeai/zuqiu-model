@echo off
REM ============================================================
REM T-005 v3 自动重训触发器 — Windows 启动脚本
REM 用于任务计划程序或手动执行
REM ============================================================

setlocal

set SCRIPT_DIR=%~dp0
set PYTHON_EXE=python
set RUNNER=%SCRIPT_DIR%retrain_trigger_runner.py

echo ============================================================
echo T-005 v3 自动重训触发器
echo 时间: %date% %time%
echo ============================================================

if "%1"=="" (
    echo.
    echo 用法:
    echo   run_trigger.bat check     检查触发条件
    echo   run_trigger.bat trigger   强制重训
    echo   run_trigger.bat daemon    守护模式
    echo   run_trigger.bat status    查看状态
    echo   run_trigger.bat schedule  安装定时任务
    echo.
    echo 执行默认操作: check
    echo.
    %PYTHON_EXE% "%RUNNER%" check
) else (
    echo.
    echo 执行命令: %1
    echo.
    %PYTHON_EXE% "%RUNNER%" %1
)

echo.
echo 完成时间: %date% %time%
pause
