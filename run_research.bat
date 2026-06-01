@echo off
REM Offline Deep Research runner — double-click to use.
cd /d "%~dp0"
python worker\run_research_local.py %*
echo.
pause
