@echo off
rem ---------------------------------------------------------------------------
rem  Foreground run (logs in this window, Ctrl+C to stop).
rem  First run bootstraps the environment exactly like start.bat does.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title Qunban console
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

call :bootstrap
if errorlevel 1 goto :failed
if /i "%~1"=="--check" (
    echo.
    echo [setup] --check only: environment verified, service not started.
    exit /b 0
)

set "VENV=.venv"
if exist ".venv312\Scripts\python.exe" set "VENV=.venv312"

echo.
echo ================================================
echo   Group Agent is starting with %VENV%
echo   http://127.0.0.1:8000
echo   Press Ctrl+C to stop.
echo ================================================
echo.

start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"
"%VENV%\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo Server stopped.
pause
exit /b 0

rem ---------------------------------------------------------------------------
:bootstrap
if exist ".venv312\Scripts\python.exe" (
    ".venv312\Scripts\python.exe" "scripts\bootstrap.py" %*
    exit /b %errorlevel%
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\bootstrap.py" %*
    exit /b %errorlevel%
)
where py >nul 2>nul
call "scripts\find-python.cmd"
if defined PY_LAUNCHER (
    %PY_LAUNCHER% "scripts\bootstrap.py" %*
    exit /b %errorlevel%
)
echo.
echo [setup] Python 3.11+ was not found on this PC.
echo         Install the 64-bit build from https://www.python.org/downloads/windows/
echo         Tick "Add python.exe to PATH", then run start-console.bat again.
echo.
exit /b 1

:failed
echo.
echo [setup] Bootstrap failed. Fix the items above, then run start-console.bat again.
echo.
pause
exit /b 1
