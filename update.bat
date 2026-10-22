@echo off
chcp 65001 >nul
title Cập nhật Antigravity Telegram Bridge

echo ==============================================================
echo   ĐANG TẢI BẢN CẬP NHẬT MỚI NHẤT TỪ GITHUB...
echo ==============================================================
echo.

git pull

echo.
if %errorlevel% equ 0 (
    echo [THÀNH CÔNG] Đã cập nhật xong code mới nhất!
    echo Bây giờ bạn có thể nhấp đúp vào run.bat để khởi chạy.
) else (
    echo [LỖI] Có lỗi khi cập nhật. Vui lòng kiểm tra lại kết nối mạng hoặc git.
)

echo.
pause
