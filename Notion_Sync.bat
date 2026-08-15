@echo off
setlocal
cd /d "%~dp0"

:: Start the background Edge Web Bridge Server silently if not running
netstat -ano | findstr :8765 >nul
if %ERRORLEVEL% neq 0 (
    start "" pythonw "%~dp0notion_server.py"
)

:: Launch the Interactive Notion Sync Dashboard
python "%~dp0notion_git_sync.py" %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo An error occurred. Press any key to exit...
    pause >nul
)
endlocal
