@echo off
rem ---------------------------------------------------------------------------
rem  Environment setup / repair only - does not start the service.
rem
rem    setup.bat                 fill in whatever is missing, then verify
rem    setup.bat --check         report only, change nothing
rem    setup.bat --force         reinstall python deps from scratch
rem    setup.bat --extras all    install every optional module
rem    setup.bat --mirror tsinghua
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title Qunban setup
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist ".venv312\Scripts\python.exe" (
    ".venv312\Scripts\python.exe" "scripts\bootstrap.py" %*
    goto :done
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\bootstrap.py" %*
    goto :done
)
call "scripts\find-python.cmd"
if defined PY_LAUNCHER (
    %PY_LAUNCHER% "scripts\bootstrap.py" %*
    goto :done
)
echo.
echo [setup] Python 3.11+ was not found on this PC.
echo         Install the 64-bit build from https://www.python.org/downloads/windows/
echo         Tick "Add python.exe to PATH", then run setup.bat again.
echo.

:done
echo.
pause
exit /b 0
