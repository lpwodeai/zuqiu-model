@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python scripts\backup_db.py
if %errorlevel% neq 0 (
    echo [ERROR] Backup failed at %date% %time%
) else (
    echo [OK] Backup completed at %date% %time%
)