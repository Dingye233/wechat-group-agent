@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" set PORT=8020
echo 启动模拟上层服务端：http://0.0.0.0:%PORT%/  （Ctrl+C 停止）
"%~dp0..\..\.venv312\Scripts\python.exe" "%~dp0mock_upstream.py" --port %PORT%
pause
