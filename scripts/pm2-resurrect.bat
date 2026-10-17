@echo off
rem ============================================================
rem  five-leagues PM2 auto-resurrect (AtLogOn scheduled task)
rem  Created 2026-09-27 (change C-20260927-024)
rem  Task name : FiveLeagues_PM2_Startup
rem  Dump file : %USERPROFILE%\.pm2\dump.pm2 (refresh via: pm2 save)
rem  Note: keep this file pure ASCII; paths with CJK chars are
rem        referenced via %~dp0 / %USERPROFILE% on purpose.
rem ============================================================

setlocal
set "PM2_HOME=%USERPROFILE%\.pm2"
set "PATH=C:\Program Files\nodejs;%PATH%"

cd /d "%~dp0.."
set "LOG=%~dp0..\logs\pm2-resurrect.log"

echo. >> "%LOG%"
echo [%date% %time%] === pm2 resurrect triggered (AtLogOn) === >> "%LOG%"
call "%APPDATA%\npm\pm2.cmd" resurrect >> "%LOG%" 2>&1
echo [%date% %time%] === pm2 resurrect finished, exit code %errorlevel% === >> "%LOG%"

endlocal
