@echo off
chcp 65001 >nul
title Antigravity Telegram Bridge

if not exist ".env" (
    echo [LOI] Khong tim thay file .env! Dang tao tu .env.example...
    copy .env.example .env
    echo Vui long mo file .env, nhap BOT_TOKEN cua ban roi chay lai run.bat!
    pause
    exit /b
)

if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe bridge.py
) else (
    python bridge.py
)

if %errorlevel% neq 0 (
    echo [THONG BAO] Bot da dung lai.
    pause
)
