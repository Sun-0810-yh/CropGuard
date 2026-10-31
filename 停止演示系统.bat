@echo off
chcp 65001 >nul
title 停止 CropGuard 演示系统
cd /d "%~dp0"

echo 正在关闭占用 8080 / 8443 的 Python 进程（不会动 Ollama）...
"D:\Dev Env\Python\torch\venv\Scripts\python.exe" -B tool/port_check.py --kill

echo.
echo 已尝试关闭。若「CropGuard 服务器」窗口还开着，直接点它的 × 也行。
pause
