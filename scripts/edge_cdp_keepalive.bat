@echo off
rem C-112-0d: Unattended Edge CDP keepalive for scheduled 19:00 lineup refresh.
rem ASCII-only comments on purpose: cmd.exe parses .bat as ANSI(GBK);
rem UTF-8 Chinese breaks parsing (root cause of the old start_edge_sofa.bat failure).
rem Safe to run any time: kills residual Edge, restarts with CDP port 9222.

taskkill /f /im msedge.exe >nul 2>&1
timeout /t 2 /nobreak >nul

set PROFILE=F:\zuqiu\edge_sofa_profile
if not exist %PROFILE% mkdir %PROFILE%

set EDGE1=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
set EDGE2=C:\Program Files\Microsoft\Edge\Application\msedge.exe

if exist "%EDGE1%" (
    start "" "%EDGE1%" --remote-debugging-port=9222 --user-data-dir=%PROFILE% https://www.sofascore.com/
    goto :done
)
if exist "%EDGE2%" (
    start "" "%EDGE2%" --remote-debugging-port=9222 --user-data-dir=%PROFILE% https://www.sofascore.com/
    goto :done
)
echo [ERROR] msedge.exe not found in standard paths.
exit /b 1
:done
rem Wait for CDP endpoint to come up, then verify once.
timeout /t 5 /nobreak >nul
python "F:\zuqiu\五大联赛专属模型\五大联赛专属模型\scripts\check_cdp_alive.py" --tag bat-restart
exit /b 0
