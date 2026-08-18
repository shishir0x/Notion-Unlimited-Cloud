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

REM Check Node.js for Next.js
node --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Node.js is not installed or not in PATH.
    echo Please install Node.js 18+ from https://nodejs.org
    echo.
    pause
    exit /b 1
)

REM Install Next.js dependencies if needed
if not exist "%~dp0notion-drive-app\node_modules" (
    echo Installing Next.js dependencies...
    cd /d "%~dp0notion-drive-app"
    npm install
    cd /d "%~dp0"
)

REM Copy .env to Next.js app if not present
if not exist "%~dp0notion-drive-app\.env.local" (
    echo Creating .env.local for Next.js app...
    copy "%~dp0.env" "%~dp0notion-drive-app\.env.local" >nul 2>&1
)

REM Start Next.js dev server in background
echo Starting Next.js web app...
start "Next.js Drive App" /B cmd /c "cd /d \"%~dp0notion-drive-app\" && npm run dev"

REM Wait for Next.js to be ready
echo Waiting for Next.js to start...
timeout /t 5 /nobreak >nul

REM Open browser to the web app
echo Opening Notion Drive in browser...
start http://localhost:3000

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
