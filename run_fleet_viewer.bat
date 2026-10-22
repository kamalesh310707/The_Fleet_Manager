@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set PYTHONPATH=%~dp0
cd /d "%~dp0"
call "%~dp0venv\Scripts\activate.bat"
python -u -m robots.run_fleet_viewer
if %errorlevel% neq 0 (
    echo.
    echo Fleet viewer exited with error code %errorlevel%
    pause
)
