@echo off
cd /d "%~dp0"
title Muse 视频工作台 (本地免 Docker 运行器)

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python 环境，请先安装 Python (务必勾选 Add Python to PATH)
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

python run_local.py %*
pause
