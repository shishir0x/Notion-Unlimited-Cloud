@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title Notion Unlimited Cloud

set "LOCAL_SERVER_PORT=8765"
set "PYTHONIOENCODING=utf-8"

:: ── Check Python ──────────────────────────────────────────────────────────────
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERROR] Python is not installed or not in PATH.
    echo  Please install Python 3.10+ from https://www.python.org/downloads/
    echo  Make sure to tick "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: ── Install dependencies (silent if already installed) ────────────────────────
echo  Checking dependencies...
pip install -r "%~dp0requirements.txt" -q --disable-pip-version-check
if %ERRORLEVEL% neq 0 (
    echo  [WARN] Could not install some dependencies. Continuing anyway...
)

:: ── First-run setup (only if .env is missing) ─────────────────────────────────
if not exist "%~dp0.env" (
    echo.
    echo  No .env file found. Running first-time setup wizard...
    echo.
    python "%~dp0setup.py"
    if %ERRORLEVEL% neq 0 (
        echo.
        echo  Setup was not completed. Run 'python setup.py' to configure.
        pause
        exit /b 1
    )
)

:: ── Start background Web Drive GUI server (if not already running) ─────────────
netstat -ano | findstr ":8765 " >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  Starting Notion Web Drive server...
    start "" /B pythonw "%~dp0notion_server.py"
    timeout /t 2 /nobreak >nul
)

:: ── Open Google Drive-style Web GUI in default browser ────────────────────────
echo  Opening Notion Web Drive in your browser...
start http://127.0.0.1:8765

:: ── Launch interactive terminal CLI (if arguments passed or user wants CLI) ───
if "%~1"=="" (
    python "%~dp0notion_sync.py"
) else (
    python "%~dp0notion_sync.py" %*
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo  An error occurred. Check the output above.
    pause
)

endlocal
