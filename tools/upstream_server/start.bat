@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" set PORT=8030
echo 上层调度服务：http://127.0.0.1:%PORT%/   （Swagger: /docs，Ctrl+C 停止）
"%~dp0..\..\.venv312\Scripts\python.exe" "%~dp0app.py" --port %PORT%
pause
