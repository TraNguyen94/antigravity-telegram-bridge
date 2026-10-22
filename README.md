# 🚀 Antigravity Telegram Bridge

> **Cầu nối điều khiển Google Antigravity IDE từ xa qua Telegram Bot**  
> Cho phép bạn ra lệnh, gửi ảnh, chọn dự án, chuyển đoạn chat và nhận kết quả trực tiếp từ điện thoại hoặc bất kỳ thiết bị nào thông qua Telegram.

---

## ✨ Tính Năng Nổi Bật

- 🤖 **Điều khiển 100% Antigravity IDE từ xa:** Gõ lệnh từ Telegram, bot sẽ tự động đưa vào ô chat của IDE và trả về kết quả theo thời gian thực.
- 📂 **Quản lý đa dự án:** Liệt kê toàn bộ dự án trên máy tính, gợi ý nhanh 5 dự án gần nhất qua phím tắt `/p1` đến `/p5`.
- 💬 **Chuyển đoạn chat 1 chạm:** Duyệt toàn bộ lịch sử các đoạn chat (conversations) trong từng dự án và chuyển IDE sang đoạn chat đó ngay lập tức.
- 📸 **Gửi ảnh trực tiếp từ điện thoại:** Chụp ảnh màn hình, hình vẽ hoặc tài liệu gửi vào Telegram, bot tự động nạp ảnh vào Antigravity IDE.
- ⚡ **Tự động dò cổng DevTools (Auto-detect CDP Port):** Tự động phát hiện cổng kết nối của Antigravity IDE ngay cả khi khởi động lại máy hoặc đổi cổng.
- 🔒 **Bảo mật phân quyền:** Chỉ cho phép Telegram User ID được chỉ định điều khiển bot, ngăn chặn hoàn toàn truy cập trái phép.
- 💻 **Chạy mượt mà đa nền tảng:** Hỗ trợ Windows, macOS và Linux.

---

## 📋 Yêu Cầu Hệ Thống

1. **Python 3.10+** (đã cài đặt và thêm vào PATH).
2. **Google Antigravity IDE** đang chạy trên máy tính.
3. Một tài khoản Telegram và **Bot Token** tạo từ [@BotFather](https://t.me/BotFather).

---

## 🛠 Hướng Dẫn Cài Đặt Trên Máy Mới

### Bước 1: Tải mã nguồn về máy

```bash
git clone https://github.com/TraNguyen94/antigravity-telegram-bridge.git
cd antigravity-telegram-bridge
```

---

### Bước 2: Chuẩn bị Telegram Bot & User ID

1. **Lấy Bot Token:**
   - Mở Telegram, tìm bot **[@BotFather](https://t.me/BotFather)**.
   - Gõ lệnh `/newbot` và làm theo hướng dẫn để đặt tên bot.
   - Sao chép chuỗi mã Token (ví dụ: `8884877895:AAFUpGHMul8wLTVIA7ioYZePYZAiROooPxk`).

2. **Lấy User ID của bạn:**
   - Tìm bot **[@userinfobot](https://t.me/userinfobot)** trên Telegram.
   - Nhấn `/start`, bot sẽ phản hồi ID dạng số của bạn (ví dụ: `5889330916`).

---

### Bước 3: Cấu hình file `.env`

Sao chép file `.env.example` thành `.env`:

```bash
cp .env.example .env     # Trên Linux / macOS
copy .env.example .env   # Trên Windows
```

Mở file `.env` và điền thông tin của bạn:

```env
BOT_TOKEN=8884877895:AAFUpGHMul8wLTVIA7ioYZePYZAiROooPxk
ALLOWED_USER_IDS=5889330916
CDP_PORT=57061
```

---

### Bước 4: Cài đặt thư viện & Khởi chạy

#### 👉 Dành cho Windows:
Chỉ cần nhấp đúp vào file **`install.bat`** để cài đặt, sau đó nhấp đúp vào **`run.bat`** để khởi chạy bot.

Hoặc chạy thủ công qua PowerShell / Command Prompt:
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python bridge.py
```

#### 👉 Dành cho Linux / macOS:
```bash
chmod +x run.sh
./run.sh
```

---

## 🎮 Danh Sách Lệnh Điều Khiển Trên Telegram

| Lệnh | Ý nghĩa |
| :--- | :--- |
| `/projects` | Xem danh sách tất cả các dự án trên Antigravity IDE |
| `/chats` | Xem danh sách các đoạn chat gần nhất và chuyển đổi nhanh |
| `/p1` .. `/p5` | Phím tắt chọn nhanh 5 dự án gần đây nhất |
| `/current` | Xem dự án và đoạn chat hiện tại đang mở trên PC |
| `/new` | Tạo một phiên đoạn chat hoàn toàn mới trên IDE |
| `/stop` | Dừng khẩn cấp lệnh/tác vụ đang chạy trên PC |
| `/help` | Xem bảng hướng dẫn và trợ giúp |

> **💡 Mẹo:** Khi bạn gõ ký tự `/` trong khung chat Telegram với bot, Telegram sẽ **tự động hiện popup gợi ý 5 dự án gần nhất** để bạn bấm chọn nhanh!

---

## ⚙️ Hướng Dẫn Chạy Bot Tự Động Cùng Windows (Auto-start)

Để bot tự khởi động ngầm mỗi khi bạn bật máy tính:

1. Nhấn tổ hợp phím **`Windows + R`**, nhập:
   ```text
   shell:startup
   ```
   và nhấn **Enter**.
2. Nhấp chuột phải vào file **`run.bat`** trong thư mục dự án ➔ chọn **Create shortcut** (Tạo lối tắt).
3. Kéo thả file Shortcut vừa tạo vào thư mục Startup vừa mở.
4. Từ giờ, mỗi khi máy tính bật lên, bot sẽ tự động chạy ngầm phục vụ bạn!

---

## 📄 Giấy Phép & Tác Quyền

Dự án phát triển bởi **Trúc Trà** • Tự do sử dụng và tùy biến cho mục đích cá nhân và thương mại.
