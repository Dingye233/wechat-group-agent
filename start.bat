@echo off
rem ---------------------------------------------------------------------------
rem  Qunban launcher (double-click me).
rem
rem  First run bootstraps everything a fresh clone is missing: virtualenv,
rem  python deps, .env, data/ folders and the sqlite schema (scripts\bootstrap.py).
rem  Later runs just verify quickly and start the service.
rem
rem  Extra args are forwarded to bootstrap.py, e.g.
rem      start.bat --extras all       install every optional module
rem      start.bat --with-dsh         also install the DSH runtime (~700MB)
rem      start.bat --mirror tsinghua  use a PyPI mirror
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title Qunban launcher
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
echo   Group Agent is starting in the background
echo   http://127.0.0.1:8000
echo   Close the tray window to hide it; right-click
echo   the tray icon to exit.
echo   For foreground logs use start-console.bat.
echo ================================================
echo.

start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tray.ps1"
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
call "scripts\find-python.cmd"
if defined PY_LAUNCHER (
    %PY_LAUNCHER% "scripts\bootstrap.py" %*
    exit /b %errorlevel%
)
echo.
echo [setup] Python 3.11+ was not found on this PC.
echo         Install the 64-bit build from https://www.python.org/downloads/windows/
echo         Tick "Add python.exe to PATH", then run start.bat again.
echo.
exit /b 1

:failed
echo.
echo [setup] Bootstrap failed. Fix the items above, then run start.bat again.
echo         Need details only? Run: python scripts\bootstrap.py --check
echo.
pause
exit /b 1
