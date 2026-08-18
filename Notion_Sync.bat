@echo off
setlocal
cd /d "%~dp0"
title Notion Unlimited Cloud

set "PYTHONIOENCODING=utf-8"

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ and make sure to tick 'Add Python to PATH'.
    echo.
    pause
    exit /b 1
)

REM Install Python requirements if needed
pip install -r "%~dp0requirements.txt" -q --disable-pip-version-check

REM Run unified application launcher
python "%~dp0launcher.py" %*

if errorlevel 1 (
    echo.
    echo Application stopped.
    pause
)

endlocal
