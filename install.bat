@echo off
chcp 65001 >nul
echo ==============================================================
echo   CAI DAT ANTIGRAVITY TELEGRAM BRIDGE
echo ==============================================================

if not exist ".env" (
    echo [INFO] Tao file .env tu .env.example...
    copy .env.example .env
    echo [CHU Y] Vui long mo file .env va dien BOT_TOKEN cua ban!
)

if not exist ".venv" (
    echo [INFO] Tao moi moi truong ao Python (.venv)...
    python -m venv .venv
)

echo [INFO] Cai dat thu vien tu requirements.txt...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo.
echo ==============================================================
echo [THANH CONG] Cai dat hoan tat! Chay run.bat de bat dau.
echo ==============================================================
pause
