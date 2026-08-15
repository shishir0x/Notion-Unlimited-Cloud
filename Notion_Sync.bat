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

REM Install dependencies
echo Checking dependencies...
pip install -r "%~dp0requirements.txt" -q --disable-pip-version-check
if errorlevel 1 (
    echo [WARN] Could not install some dependencies. Continuing...
)

REM First-run setup if .env is missing
if not exist "%~dp0.env" (
    echo.
    echo No .env file found. Running first-time setup wizard...
    echo.
    python "%~dp0setup.py"
    if errorlevel 1 (
        echo.
        echo Setup was not completed.
        pause
        exit /b 1
    )
)

REM Start background Web Drive GUI server if not running
netstat -ano | findstr ":8765 " >nul 2>&1
if errorlevel 1 (
    echo Starting Notion Web Drive server...
    start "" /B pythonw "%~dp0notion_server.py"
    timeout /t 2 /nobreak >nul
)

REM Open Web GUI in default browser
echo Opening Notion Web Drive in browser...
start http://127.0.0.1:8765

REM Launch terminal sync CLI
if "%~1"=="" (
    python "%~dp0notion_sync.py"
) else (
    python "%~dp0notion_sync.py" %*
)

if errorlevel 1 (
    echo.
    echo Sync exited with an error.
    pause
)

endlocal
