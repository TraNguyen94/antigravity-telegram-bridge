#!/usr/bin/env bash
set -e

if [ ! -f ".env" ]; then
    echo "[INFO] Tạo file .env từ .env.example..."
    cp .env.example .env
    echo "[CHÚ Ý] Vui lòng chỉnh sửa file .env và nhập BOT_TOKEN của bạn!"
fi

if [ ! -d ".venv" ]; then
    echo "[INFO] Khởi tạo môi trường ảo Python (.venv)..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

echo "[INFO] Khởi chạy Antigravity Telegram Bridge..."
.venv/bin/python bridge.py
