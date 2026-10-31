@echo off
chcp 65001 >nul
title CropGuard 评分 GUI（20 样本 / 180 秒）
cd /d "%~dp0"

echo 正在打开评分 GUI（约 5-10 秒）...
REM 推理环境固定为这一个（GPU 版），不做备选
set "CG_DEVICE="
"D:\Dev Env\Python\torch\venv\Scripts\python.exe" -B main.py


echo.
echo GUI 已退出。评分报告在 output\ 目录下（未导出时程序会自动静默导出一份）。
pause
