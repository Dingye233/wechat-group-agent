@echo off
setlocal
cd /d "%~dp0"
title Qunban launcher

set "VENV=.venv"
if exist ".venv312\Scripts\python.exe" set "VENV=.venv312"

if not exist "%VENV%\Scripts\python.exe" (
  echo [setup] Creating Python virtual environment...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    where python >nul 2>nul
    if errorlevel 1 (
      echo Python was not found. Install Python 3.11+ and try again.
      pause
      exit /b 1
    )
    python -m venv .venv
  )
  if errorlevel 1 (
    echo Failed to create .venv
    pause
    exit /b 1
  )
  set "VENV=.venv"
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
  "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
  )
)

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
)

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
