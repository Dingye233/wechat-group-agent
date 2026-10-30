@echo off
rem ---------------------------------------------------------------------------
rem  Find a usable Python launcher for the batch launchers.
rem  Call it from another .bat:  call "scripts\find-python.cmd"
rem  It sets PY_LAUNCHER (e.g. "py -3.12") or leaves it empty.
rem
rem  Order matters: prefer 3.12 / 3.11 because some dependencies have no wheels
rem  for the newest interpreter yet, and `py -3` may resolve to 3.13/3.14.
rem ---------------------------------------------------------------------------
set "PY_LAUNCHER="

py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY_LAUNCHER=py -3.12"

if not defined PY_LAUNCHER (
    py -3.11 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY_LAUNCHER=py -3.11"
)
if not defined PY_LAUNCHER (
    py -3.13 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY_LAUNCHER=py -3.13"
)
if not defined PY_LAUNCHER (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY_LAUNCHER=py -3"
)
if not defined PY_LAUNCHER (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY_LAUNCHER=python"
)
exit /b 0
