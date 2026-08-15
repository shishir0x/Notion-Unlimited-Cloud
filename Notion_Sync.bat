@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Notion Unlimited Cloud

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
netstat -ano | findstr :%LOCAL_SERVER_PORT% >nul 2>&1
if %ERRORLEVEL% neq 0 (
    start "" /B pythonw "%~dp0notion_server.py"
    timeout /t 1 /nobreak >nul
)

:: ── Launch the interactive sync dashboard ─────────────────────────────────────
python "%~dp0notion_sync.py" %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo  An error occurred. Check the output above.
    pause
)

endlocal
