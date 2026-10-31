@echo off
chcp 65001 >nul
title CropGuard 演示系统启动器
cd /d "%~dp0"

echo ============================================
echo    株得CropGuard - 演示系统
echo ============================================
echo.

REM 1) 推理环境固定为这一个（GPU 版），不做备选
set "PY_EXE=D:\Dev Env\Python\torch\venv\Scripts\python.exe"
set "CG_DEVICE="
"%PY_EXE%" -c "import torch, cv2, fastapi, ultralytics; raise SystemExit(0 if torch.cuda.is_available() else 1)" >nul 2>nul
if errorlevel 1 (
  echo [错误] 推理环境不可用：%PY_EXE%
  echo        确认环境存在、显卡驱动正常后再试。
  pause
  exit /b 1
)
set PY="%PY_EXE%"

REM 3) Ollama 在不在（问答要用；不在只提示，不阻塞演示）
powershell -NoProfile -Command "try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:11434/ | Out-Null; exit 0}catch{exit 1}" >nul 2>nul
if errorlevel 1 (
  echo [提示] 本地大模型 Ollama 没在跑：问答会降级为知识库原文（不影响其它演示）。
  echo        要开：开始菜单搜 Ollama 打开，或另开窗口执行  ollama serve
  echo.
)

REM 3.5) 服务器已经在跑？直接开指挥台，别起第二个（端口会冲突）
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8080/api/health; if($r.StatusCode -eq 200){exit 0}else{exit 1}}catch{exit 1}" >nul 2>nul
if not errorlevel 1 (
  echo [提示] 服务器已经在运行，直接打开演示指挥台。
  call :open_demo
  ping -n 5 127.0.0.1 >nul
  exit /b 0
)

REM 4) 起服务器（单独窗口，日志看得见；关这个窗口 = 关服务器）
echo [1/2] 正在启动服务器（首次加载模型约 10-20 秒）...
start "CropGuard 服务器（别关这个窗口）" cmd /k "%PY% -B api_server.py"

REM 5) 等 8080 就绪（每次探测约 3 秒，40 次 ≈ 2 分钟；冷机器/杀毒扫描会慢）
echo [2/2] 等待服务器就绪...
set /a tries=0
:wait
set /a tries+=1
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8080/api/health; if($r.StatusCode -eq 200){exit 0}else{exit 1}}catch{exit 1}" >nul 2>nul
if not errorlevel 1 goto ready
if %tries% GEQ 40 (
  echo.
  echo [错误] 约 2 分钟还没起来。看「CropGuard 服务器」窗口里的报错。
  echo        最常见：8080 被占用 - 先双击「停止演示系统.bat」，再重新双击本文件。
  pause
  exit /b 1
)
ping -n 3 127.0.0.1 >nul
goto wait

:ready
call :open_demo
echo 手机端地址在第三张卡片上（扫码即可）。
echo 演示结束：双击「停止演示系统.bat」。
ping -n 7 127.0.0.1 >nul
exit /b 0

REM 打开指挥台：两个分支共用。这里只负责"开浏览器 + 把地址留在屏幕上"，
REM 万一现场机器的默认浏览器关联坏了（start 会静默失败），操作员照着地址手打也能进去。
:open_demo
start "" "http://127.0.0.1:8080/demo"
ping -n 4 127.0.0.1 >nul
echo.
echo 已请求打开「演示指挥台」：http://127.0.0.1:8080/demo
echo   如果浏览器没有自动弹出来，请手动打开上面这个地址。
exit /b 0

