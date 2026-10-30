import asyncio
import ctypes
from ctypes import wintypes
import datetime
import glob
import json
import logging
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

from PIL import Image
import psutil
import requests
import websockets

import config

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("AntigravityBridge")

CDP_LIST_URL = f"http://127.0.0.1:{config.DEFAULT_CDP_PORT}/json/list"
message_queue = asyncio.Queue()
user_nav_context = {}

# -------------------------------------------------------------
# Telegram Messaging Helpers
# -------------------------------------------------------------
def send_telegram(chat_id, text, reply_to_id=None, reply_markup=None):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    if not chunks:
        chunks = ["(Trống)"]
    for idx, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown"
        }
        if reply_to_id:
            payload["reply_to_message_id"] = reply_to_id
        if reply_markup and idx == len(chunks) - 1:
            payload["reply_markup"] = json.dumps(reply_markup)
            
        try:
            data = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data)
            urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError:
            # Fallback without markdown if special characters cause parsing errors
            payload.pop("parse_mode", None)
            data = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data)
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            logger.error(f"Error sending message to Telegram: {e}")

def send_telegram_photo(chat_id, photo_path, caption=None, reply_to_id=None):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendPhoto"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption[:1024]
        data["parse_mode"] = "Markdown"
    if reply_to_id:
        data["reply_to_message_id"] = reply_to_id
    try:
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            resp = requests.post(url, data=data, files=files, timeout=30)
            res_json = resp.json()
            if not res_json.get("ok"):
                # Fallback without markdown
                data.pop("parse_mode", None)
                f.seek(0)
                resp = requests.post(url, data=data, files={"photo": f}, timeout=30)
                res_json = resp.json()
            return res_json
    except Exception as e:
        logger.error(f"Error sending photo to Telegram: {e}")
        return None

def answer_callback_query(cb_id, text=None, show_alert=False):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": cb_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = "true"
    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error(f"Error answering callback query: {e}")

def send_telegram_chat_action(chat_id, action="typing"):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendChatAction"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "action": action}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload)
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def download_telegram_file(file_id, dest_dir=config.RECEIVED_IMAGES_DIR):
    os.makedirs(dest_dir, exist_ok=True)
    get_file_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/getFile?file_id={file_id}"
    req = urllib.request.Request(get_file_url)
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode())
    if not data.get("ok"):
        raise Exception(f"Không thể lấy thông tin file từ Telegram: {data}")
    remote_path = data["result"]["file_path"]
    filename = f"tg_{int(time.time())}_{os.path.basename(remote_path)}"
    local_path = os.path.join(dest_dir, filename)
    
    download_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{remote_path}"
    req_dl = urllib.request.Request(download_url)
    with urllib.request.urlopen(req_dl, timeout=30) as r, open(local_path, "wb") as f:
        f.write(r.read())
    logger.info(f"Downloaded Telegram photo to {local_path} ({os.path.getsize(local_path)} bytes)")
    return local_path

# -------------------------------------------------------------
# Native Windows Screen Capture & Hardware Management
# -------------------------------------------------------------
def capture_screen_gdi(output_path):
    """Chụp ảnh màn hình Desktop Windows bằng Win32 GDI BitBlt siêu nhanh và sắc nét."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    width = user32.GetSystemMetrics(0)   # SM_CXSCREEN
    height = user32.GetSystemMetrics(1)  # SM_CYSCREEN

    hdesktop = user32.GetDesktopWindow()
    desktop_dc = user32.GetDC(hdesktop)
    mem_dc = gdi32.CreateCompatibleDC(desktop_dc)
    bitmap = gdi32.CreateCompatibleBitmap(desktop_dc, width, height)
    old_bitmap = gdi32.SelectObject(mem_dc, bitmap)

    # SRCCOPY = 0x00CC0020, CAPTUREBLT = 0x40000000
    gdi32.BitBlt(mem_dc, 0, 0, width, height, desktop_dc, 0, 0, 0x00CC0020 | 0x40000000)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ('biSize', wintypes.DWORD),
            ('biWidth', wintypes.LONG),
            ('biHeight', wintypes.LONG),
            ('biPlanes', wintypes.WORD),
            ('biBitCount', wintypes.WORD),
            ('biCompression', wintypes.DWORD),
            ('biSizeImage', wintypes.DWORD),
            ('biXPelsPerMeter', wintypes.LONG),
            ('biYPelsPerMeter', wintypes.LONG),
            ('biClrUsed', wintypes.DWORD),
            ('biClrImportant', wintypes.DWORD)
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buffer_len = width * height * 4
    buf = (ctypes.c_char * buffer_len)()

    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buf, ctypes.byref(bmi), 0)

    gdi32.SelectObject(mem_dc, old_bitmap)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hdesktop, desktop_dc)

    img = Image.frombuffer('RGBA', (width, height), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, 'JPEG', quality=85)
    return width, height

def get_system_hardware_info():
    """Thu thập thông số phần cứng thực tế của PC."""
    cpu = psutil.cpu_percent(interval=0.4)
    cores = psutil.cpu_count(logical=True)
    mem = psutil.virtual_memory()
    
    disks_info = []
    for part in psutil.disk_partitions(all=False):
        if 'cdrom' in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            used_gb = round(usage.used / (1024**3), 1)
            total_gb = round(usage.total / (1024**3), 1)
            disks_info.append(f"  • `{part.device}` {used_gb}/{total_gb} GB ({usage.percent}%)")
        except PermissionError:
            pass
            
    boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.datetime.now() - boot_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "127.0.0.1"
        
    battery = psutil.sensors_battery()
    bat_str = f"{battery.percent}% ({'Đang sạc 🔌' if battery.power_plugged else 'Dùng pin 🔋'})" if battery else "Nguồn điện PC cắm trực tiếp 🔌"
    
    mem_used_gb = round(mem.used / (1024**3), 2)
    mem_total_gb = round(mem.total / (1024**3), 2)
    
    report = (
        f"💻 *THÔNG SỐ HỆ THỐNG MÁY TÍNH (PC STATUS):*\n\n"
        f"• *Tên máy:* `{hostname}` (`{local_ip}`)\n"
        f"• *Thời gian mở máy (Uptime):* `{hours} giờ {minutes} phút`\n"
        f"• *CPU:* `{cpu}%` ({cores} Cores/Threads)\n"
        f"• *RAM:* `{mem_used_gb} GB / {mem_total_gb} GB` ({mem.percent}%)\n"
        f"• *Pin / Nguồn:* {bat_str}\n"
        f"• *Dung lượng ổ đĩa:*\n" + "\n".join(disks_info)
    )
    return report

def lock_windows_workstation():
    """Khóa màn hình máy tính Windows ngay lập tức."""
    return ctypes.windll.user32.LockWorkStation() != 0

def execute_system_cmd(command_str):
    """Chạy lệnh PowerShell / CMD trực tiếp trên PC và trả stdout/stderr."""
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command_str],
            capture_output=True,
            text=True,
            timeout=35
        )
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        if not out and not err:
            return "✅ Lệnh đã thực thi thành công (Không có text xuất ra)."
        combined = []
        if out:
            combined.append(f"📄 *STDOUT:*\n```text\n{out[:3500]}\n```")
        if err:
            combined.append(f"⚠️ *STDERR:*\n```text\n{err[:1000]}\n```")
        return "\n\n".join(combined)
    except subprocess.TimeoutExpired:
        return "⚠️ Lệnh chạy quá thời gian chờ (Timeout 35s)."
    except Exception as e:
        return f"❌ Lỗi thực thi lệnh: {e}"

def ensure_single_instance():
    """Đảm bảo chỉ có DUY NHẤT một tiến trình python bridge.py chạy trên máy tính, tránh lỗi HTTP 409 Conflict."""
    current_pid = os.getpid()
    parent_pid = os.getppid() if hasattr(os, "getppid") else None
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            p_name = (proc.info.get('name') or '').lower()
            if proc.info['pid'] not in [current_pid, parent_pid] and p_name in ['python.exe', 'pythonw.exe']:
                cmdline = " ".join(proc.info.get('cmdline') or []).lower()
                if ("bridge.py" in cmdline or "antigravity_bridge.py" in cmdline) and "-c" not in cmdline:
                    logger.info(f"Terminating old/duplicate bridge process PID: {proc.info['pid']}")
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

def wait_for_internet(timeout=300, check_interval=3):
    """Vòng lặp kiểm tra kết nối mạng khi khởi động máy. Tự động tiếp tục ngay khi có mạng."""
    logger.info("Checking internet connectivity...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        for host, port in [("8.8.8.8", 53), ("1.1.1.1", 53), ("api.telegram.org", 443)]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.5)
                s.connect((host, port))
                s.close()
                logger.info(f"Internet connection verified via {host}:{port}")
                return True
            except Exception:
                pass
        logger.info(f"Waiting for network connection... ({int(time.time() - start_time)}s)")
        time.sleep(check_interval)
    logger.warning("Internet wait timeout reached.")
    return False

def is_antigravity_running():
    for proc in psutil.process_iter(['name']):
        try:
            if 'antigravity' in (proc.info.get('name') or '').lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False

def ensure_antigravity_running():
    """Kiểm tra và tự động khởi động Google Antigravity IDE nếu chưa mở."""
    if is_antigravity_running():
        logger.info("Antigravity IDE is already running.")
        return True
    
    exe_candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "Antigravity.exe",
        Path("C:/Users/PC/AppData/Local/Programs/antigravity/Antigravity.exe")
    ]
    for exe in exe_candidates:
        if exe.exists():
            logger.info(f"Auto-launching Antigravity IDE: {exe}")
            try:
                subprocess.Popen([str(exe)], shell=False)
                for _ in range(15):
                    time.sleep(1)
                    port = get_devtools_active_port()
                    if port:
                        logger.info(f"Antigravity IDE started successfully with DevTools port: {port}")
                        time.sleep(2)
                        return True
                return True
            except Exception as e:
                logger.error(f"Error launching Antigravity IDE: {e}")
    logger.warning("Could not find Antigravity.exe to launch.")
    return False

def notify_startup(port):
    """Gửi tin nhắn Telegram thông báo máy tính đã bật và kết nối mạng thành công."""
    now_str = datetime.datetime.now().strftime("%H:%M:%S %d/%m/%Y")
    hostname = socket.gethostname()
    msg = (
        f"🚀 *ĐẠI CA TÚ ƠI! MÁY TÍNH ĐÃ KHỞI ĐỘNG & KẾT NỐI MẠNG THÀNH CÔNG!*\n\n"
        f"• *Thời gian:* `{now_str}`\n"
        f"• *Tên máy:* `{hostname}`\n"
        f"• *Google Antigravity IDE:* Đã sẵn sàng (Cổng `{port or 'Auto'}`) 🟢\n"
        f"• *Bot gác cổng:* `@tructra_hermes_pc_bot` đang ôm súng trực chiến 24/7!\n\n"
        f"👉 Đại Ca có thể gửi lệnh hoặc ảnh từ xa bất kỳ lúc nào!"
    )
    for uid in config.ALLOWED_USER_IDS:
        try:
            send_telegram(uid, msg)
        except Exception as e:
            logger.warning(f"Could not send startup greeting to {uid}: {e}")


# -------------------------------------------------------------
# Antigravity DevTools (CDP) Auto-Detection & Control
# -------------------------------------------------------------
def get_devtools_active_port():
    """Đọc chính xác cổng DevTools do Chromium cấp phát ngẫu nhiên từ file DevToolsActivePort."""
    paths = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "Antigravity" / "DevToolsActivePort")
    elif sys.platform == "darwin":
        paths.append(Path.home() / "Library" / "Application Support" / "Antigravity" / "DevToolsActivePort")
    else:
        paths.append(Path.home() / ".config" / "Antigravity" / "DevToolsActivePort")
        
    for p in paths:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                    if lines and lines[0].isdigit():
                        return int(lines[0])
            except Exception:
                pass
    return None

def detect_cdp_url():
    """Tự động dò cổng DevTools (CDP) của Antigravity IDE."""
    global CDP_LIST_URL
    
    # 1. Thử đọc file DevToolsActivePort (Chính xác 100%)
    active_port = get_devtools_active_port()
    if active_port:
        url = f"http://127.0.0.1:{active_port}/json/list"
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                tabs = json.loads(resp.read().decode())
            if isinstance(tabs, list) and any("webSocketDebuggerUrl" in t for t in tabs):
                CDP_LIST_URL = url
                logger.info(f"Connected to Antigravity port via DevToolsActivePort: {active_port}")
                return url
        except Exception:
            pass

    # 2. Thử các cổng quen thuộc
    cached_ports = [config.DEFAULT_CDP_PORT, 52874, 49912, 57061, 50717, 9222]
    for port in cached_ports:
        try:
            url = f"http://127.0.0.1:{port}/json/list"
            with urllib.request.urlopen(url, timeout=1) as resp:
                tabs = json.loads(resp.read().decode())
            if isinstance(tabs, list) and any("webSocketDebuggerUrl" in t for t in tabs):
                CDP_LIST_URL = url
                return url
        except Exception:
            pass

    # 3. Quét mạng trên Windows nếu cần
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", "Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 | Select-Object -ExpandProperty LocalPort"],
                text=True, timeout=5
            )
            for line in out.splitlines():
                line = line.strip()
                if not line.isdigit():
                    continue
                port = int(line)
                if port in cached_ports or port == active_port:
                    continue
                try:
                    url = f"http://127.0.0.1:{port}/json/list"
                    with urllib.request.urlopen(url, timeout=0.8) as resp:
                        tabs = json.loads(resp.read().decode())
                    if isinstance(tabs, list) and any("webSocketDebuggerUrl" in t for t in tabs):
                        logger.info(f"Auto-detected Antigravity CDP port via network scan: {port}")
                        CDP_LIST_URL = url
                        return url
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Error auto-detecting CDP port via PowerShell: {e}")

    return CDP_LIST_URL

def get_active_tab():
    global CDP_LIST_URL
    for attempt in range(2):
        try:
            req = urllib.request.urlopen(CDP_LIST_URL, timeout=3)
            tabs = json.loads(req.read().decode())
            for tab in tabs:
                if tab.get("type") == "page" and "webSocketDebuggerUrl" in tab:
                    return tab
            if tabs:
                return tabs[0]
        except Exception as e:
            if attempt == 0:
                logger.info("Retrying with auto-detected CDP port...")
                detect_cdp_url()
            else:
                logger.error(f"Cannot reach Antigravity DevTools: {e}")
    return None

async def cdp_eval(expression):
    tab = get_active_tab()
    if not tab:
        return None, "Không tìm thấy cửa sổ Antigravity IDE đang mở."
    ws_url = tab["webSocketDebuggerUrl"]
    try:
        async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
            msg = {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True
                }
            }
            await ws.send(json.dumps(msg))
            while True:
                res = await ws.recv()
                data = json.loads(res)
                if data.get("id") == 1:
                    val = data.get("result", {}).get("result", {}).get("value")
                    return val, None
    except Exception as e:
        return None, str(e)

async def check_is_busy():
    expr = """
    (() => {
        const cancelBtn = document.querySelector('button[aria-label="Cancel (Ctrl+D)"]') ||
                          document.querySelector('button[aria-label="Cancel Task"]');
        return !!cancelBtn;
    })()
    """
    is_busy, _ = await cdp_eval(expr)
    return bool(is_busy)

async def stop_task():
    expr = """
    (() => {
        const cancelBtn = document.querySelector('button[aria-label="Cancel (Ctrl+D)"]') ||
                          document.querySelector('button[aria-label="Cancel Task"]');
        if (cancelBtn) {
            cancelBtn.click();
            return true;
        }
        return false;
    })()
    """
    res, err = await cdp_eval(expr)
    return res, err

async def new_conversation():
    expr = """
    (() => {
        const a = document.querySelector('a[href="/"]');
        if (a) {
            a.click();
            return true;
        }
        const btn = document.querySelector('button[aria-label="New Conversation"]');
        if (btn) {
            btn.click();
            return true;
        }
        window.location.href = '/';
        return true;
    })()
    """
    res, err = await cdp_eval(expr)
    return res, err

async def get_current_conversation_id():
    expr = "window.location.pathname"
    path, _ = await cdp_eval(expr)
    if path and path.startswith("/c/"):
        return path.replace("/c/", "").split("?")[0].strip()
    return None

async def switch_to_conversation(conv_id):
    expr = f"""
    (() => {{
        const link = document.querySelector('a[href*="{conv_id}"]');
        if (link) {{
            link.click();
            return 'clicked';
        }}
        window.location.href = '/c/{conv_id}';
        return 'navigated';
    }})()
    """
    res, err = await cdp_eval(expr)
    return res, err

# -------------------------------------------------------------
# Conversation & Project Database Management
# -------------------------------------------------------------
def get_db_projects_and_conversations():
    projects = {}
    all_convs = []
    
    db_path = str(config.SUMMARIES_DB_PATH)
    default_ws = str(config.GEMINI_DIR)
    
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT conversation_id, title, workspace_uris, last_modified_time 
                FROM conversation_summaries 
                ORDER BY last_modified_time DESC;
            """)
            rows = cur.fetchall()
            conn.close()
            
            for cid, title, ws_json, mtime in rows:
                workspaces = []
                if ws_json:
                    try:
                        uris = json.loads(ws_json)
                        for u in uris:
                            parsed = urllib.parse.unquote(u)
                            if parsed.startswith("file:///"):
                                p = parsed.replace("file:///", "")
                                if len(p) > 1 and p[1] == ":":
                                    p = p[0].upper() + p[1:]
                                p = os.path.normpath(p)
                                workspaces.append(p)
                    except Exception:
                        pass
                
                ws_path = workspaces[0] if workspaces else default_ws
                proj_name = os.path.basename(ws_path) if ws_path != default_ws else ".gemini"
                
                time_str = ""
                if mtime:
                    try:
                        dt_part = mtime.split(".")[0].split("+")[0]
                        dt = datetime.datetime.strptime(dt_part, "%Y-%m-%d %H:%M:%S")
                        time_str = dt.strftime("%d/%m %H:%M")
                    except Exception:
                        time_str = mtime[:16]

                conv = {
                    "id": cid,
                    "short_id": cid[:8],
                    "title": title or "Chưa có tiêu đề",
                    "project": proj_name,
                    "path": ws_path,
                    "mtime": time_str
                }
                all_convs.append(conv)
                if proj_name not in projects:
                    projects[proj_name] = {
                        "name": proj_name,
                        "path": ws_path,
                        "conversations": []
                    }
                projects[proj_name]["conversations"].append(conv)
        except Exception as e:
            logger.error(f"Error querying conversation_summaries.db: {e}")

    for kp in config.KNOWN_PROJECT_PATHS:
        if os.path.exists(kp):
            pname = os.path.basename(kp)
            if pname not in projects:
                projects[pname] = {
                    "name": pname,
                    "path": kp,
                    "conversations": []
                }

    return projects, all_convs

def get_active_project():
    if os.path.exists(config.ACTIVE_PROJECT_FILE):
        try:
            with open(config.ACTIVE_PROJECT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"name": ".gemini", "path": str(config.GEMINI_DIR)}

def set_active_project(name, path):
    data = {
        "name": name,
        "path": path,
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        with open(config.ACTIVE_PROJECT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    return data

# -------------------------------------------------------------
# Menus & Interactive UI
# -------------------------------------------------------------
async def show_projects_menu(chat_id, reply_to_id=None):
    projects_map, _ = get_db_projects_and_conversations()
    proj_list = list(projects_map.values())[:12]
    active = get_active_project()
    
    user_nav_context[chat_id] = {
        "mode": "projects",
        "items": proj_list
    }
    
    text_lines = ["📂 *DANH SÁCH DỰ ÁN TRÊN ANTIGRAVITY IDE:*\n"]
    keyboard = []
    row = []
    
    for idx, p in enumerate(proj_list, 1):
        is_active = (p["path"].lower() == active.get("path", "").lower())
        marker = " 🟢 *[Active]*" if is_active else ""
        c_count = len(p["conversations"])
        text_lines.append(f"*{idx}.* 📁 `{p['name']}` ({c_count} đoạn chat){marker}")
        
        btn_text = f"📁 {p['name'][:18]}"
        row.append({"text": btn_text, "callback_data": f"proj:{idx-1}"})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([{"text": "💬 Xem tất cả đoạn chat gần đây", "callback_data": "menu:chats"}])
    text_lines.append("\n👉 *Gõ số* (ví dụ: `1`, `2`) hoặc *bấm nút* để xem các đoạn chat trong dự án!")
    text_lines.append("💡 *Gõ `/chats` để xem nhanh toàn bộ đoạn chat gần nhất.*")
    
    reply_markup = {"inline_keyboard": keyboard} if keyboard else None
    send_telegram(chat_id, "\n".join(text_lines), reply_to_id, reply_markup=reply_markup)

async def show_project_chats_menu(chat_id, proj, reply_to_id=None):
    convs = proj.get("conversations", [])[:10]
    curr_cid = await get_current_conversation_id()
    
    user_nav_context[chat_id] = {
        "mode": "project_chats",
        "project": proj,
        "items": convs
    }
    
    text_lines = [
        f"📂 *DỰ ÁN:* `{proj['name']}`",
        f"📁 *Thư mục:* `{proj['path']}`\n",
        f"💬 *Các đoạn chat có sẵn trong dự án ({len(convs)}):*\n"
    ]
    
    keyboard = []
    if not convs:
        text_lines.append("_(Chưa có đoạn chat nào trong dự án này)_")
    else:
        for idx, c in enumerate(convs, 1):
            is_current = (curr_cid and c["id"].startswith(curr_cid[:8]))
            marker = " 🟢 *[Đang mở]*" if is_current else ""
            text_lines.append(f"*{idx}.* `[{c['mtime']}]` *{c['title']}*{marker}")
            
            btn_text = f"💬 {c['title'][:25]}"
            keyboard.append([{"text": btn_text, "callback_data": f"conv:{c['id']}"}])
        
    keyboard.append([
        {"text": "🔙 Danh sách dự án", "callback_data": "menu:projects"},
        {"text": "💬 Tất cả đoạn chat", "callback_data": "menu:chats"}
    ])
    
    text_lines.append("\n👉 *Gõ số* (ví dụ: `1`) hoặc *bấm nút* để chuyển IDE sang đoạn chat này!")
    text_lines.append("🔙 Gõ `/projects` để quay lại danh sách dự án.")
    
    reply_markup = {"inline_keyboard": keyboard}
    send_telegram(chat_id, "\n".join(text_lines), reply_to_id, reply_markup=reply_markup)

async def show_all_chats_menu(chat_id, reply_to_id=None):
    _, all_convs = get_db_projects_and_conversations()
    convs = all_convs[:10]
    curr_cid = await get_current_conversation_id()
    
    user_nav_context[chat_id] = {
        "mode": "all_chats",
        "items": convs
    }
    
    text_lines = ["💬 *DANH SÁCH ĐOẠN CHAT GẦN ĐÂY TRÊN IDE:*\n"]
    keyboard = []
    
    for idx, c in enumerate(convs, 1):
        is_current = (curr_cid and c["id"].startswith(curr_cid[:8]))
        marker = " 🟢 *[Đang mở]*" if is_current else ""
        text_lines.append(f"*{idx}.* 📁 `[{c['project']}]` *{c['title']}* `({c['mtime']})`{marker}")
        
        btn_text = f"[{c['project'][:10]}] {c['title'][:20]}"
        keyboard.append([{"text": btn_text, "callback_data": f"conv:{c['id']}"}])
        
    keyboard.append([{"text": "📂 Xem theo Dự án (Projects)", "callback_data": "menu:projects"}])
    text_lines.append("\n👉 *Gõ số* (ví dụ: `1`, `2`) hoặc *bấm nút* để IDE chuyển ngay sang đoạn chat đó!")
    text_lines.append("💡 Hoặc gõ `/chat <tên>` để tìm kiếm nhanh.")
    
    reply_markup = {"inline_keyboard": keyboard}
    send_telegram(chat_id, "\n".join(text_lines), reply_to_id, reply_markup=reply_markup)

async def do_switch_conversation(chat_id, conv, reply_to_id=None):
    send_telegram(chat_id, f"🔄 Đang chuyển Antigravity IDE sang đoạn chat: *{conv['title']}*...", reply_to_id)
    await switch_to_conversation(conv["id"])
    if conv.get("project") and conv.get("path"):
        set_active_project(conv["project"], conv["path"])
    user_nav_context.pop(chat_id, None)
    await asyncio.sleep(2.0)
    
    confirm_text = (
        f"✅ *ĐÃ CHUYỂN ĐOẠN CHAT TRÊN IDE THÀNH CÔNG!*\n\n"
        f"• *Dự án:* `{conv.get('project', 'Chưa rõ')}`\n"
        f"• *Thư mục:* `{conv.get('path', 'Chưa rõ')}`\n"
        f"• *Đoạn chat:* *{conv['title']}*\n"
        f"• *ID:* `{conv['id']}`\n\n"
        f"👉 *Bây giờ gửi bất kỳ lệnh hoặc hình ảnh nào, Antigravity sẽ tiếp tục xử lý công việc ngay trong đoạn chat này!*"
    )
    send_telegram(chat_id, confirm_text, reply_to_id)

async def handle_callback_query(chat_id, data, msg_id):
    if data == "menu:projects":
        await show_projects_menu(chat_id, msg_id)
    elif data == "menu:chats":
        await show_all_chats_menu(chat_id, msg_id)
    elif data.startswith("proj:"):
        idx_str = data.split(":", 1)[1]
        projects_map, _ = get_db_projects_and_conversations()
        proj_list = list(projects_map.values())
        if idx_str.isdigit():
            idx = int(idx_str)
            if 0 <= idx < len(proj_list):
                await show_project_chats_menu(chat_id, proj_list[idx], msg_id)
                return
        if idx_str in projects_map:
            await show_project_chats_menu(chat_id, projects_map[idx_str], msg_id)
    elif data.startswith("conv:"):
        cid = data.split(":", 1)[1]
        _, all_convs = get_db_projects_and_conversations()
        matched = next((c for c in all_convs if c["id"] == cid or c["short_id"] == cid), None)
        if matched:
            await do_switch_conversation(chat_id, matched, msg_id)
        else:
            await do_switch_conversation(chat_id, {"id": cid, "title": cid[:8], "project": "Unknown", "path": ""}, msg_id)

# -------------------------------------------------------------
# Transcript & Execution Response Streamer
# -------------------------------------------------------------
def get_latest_transcript_info(target_cid=None):
    brain_dir = str(config.BRAIN_DIR)
    if target_cid:
        target_file = os.path.join(brain_dir, target_cid, ".system_generated", "logs", "transcript.jsonl")
        latest_file = target_file if os.path.exists(target_file) else None
    else:
        pattern = os.path.join(brain_dir, "*", ".system_generated", "logs", "transcript.jsonl")
        files = glob.glob(pattern)
        latest_file = max(files, key=os.path.getmtime) if files else None

    if not latest_file:
        return None, -1
        
    max_step = -1
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    step = d.get("step_index", -1)
                    if step > max_step:
                        max_step = step
                except Exception:
                    pass
    except Exception:
        pass
    return latest_file, max_step

def get_response_from_transcript(filepath, since_step):
    if not filepath or not os.path.exists(filepath):
        return None
    latest_response = None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    step = d.get("step_index", -1)
                    if step > since_step:
                        if d.get("source") == "MODEL" and d.get("type") == "PLANNER_RESPONSE" and d.get("content") and not d.get("tool_calls"):
                            latest_response = d.get("content")
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error reading transcript: {e}")
    return latest_response

async def inject_and_send(prompt, image_paths=None):
    tab = get_active_tab()
    if not tab:
        return False, "Không tìm thấy cửa sổ Antigravity IDE đang mở."
    ws_url = tab["webSocketDebuggerUrl"]
    
    try:
        async with websockets.connect(ws_url, max_size=20*1024*1024) as ws:
            msg_counter = 0
            async def cdp_call(method, params=None):
                nonlocal msg_counter
                msg_counter += 1
                cid = msg_counter
                payload = {"id": cid, "method": method}
                if params:
                    payload["params"] = params
                await ws.send(json.dumps(payload))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    if data.get("id") == cid:
                        return data

            # 1. Nạp ảnh vào IDE nếu có
            if image_paths:
                valid_paths = [os.path.abspath(p) for p in image_paths if os.path.exists(p)]
                if valid_paths:
                    logger.info(f"Đang đính kèm {len(valid_paths)} ảnh vào Antigravity: {valid_paths}")
                    await cdp_call("DOM.enable")
                    doc = await cdp_call("DOM.getDocument")
                    root_id = doc.get("result", {}).get("root", {}).get("nodeId", 1)
                    qs = await cdp_call("DOM.querySelector", {
                        "nodeId": root_id,
                        "selector": 'input[type="file"]'
                    })
                    file_node_id = qs.get("result", {}).get("nodeId")
                    if file_node_id:
                        await cdp_call("DOM.setFileInputFiles", {
                            "files": valid_paths,
                            "nodeId": file_node_id
                        })
                        dispatch_expr = """
                        (() => {
                            const fi = document.querySelector('input[type="file"]');
                            if (fi) {
                                fi.dispatchEvent(new Event('change', { bubbles: true }));
                                return true;
                            }
                            return false;
                        })()
                        """
                        await cdp_call("Runtime.evaluate", {
                            "expression": dispatch_expr,
                            "returnByValue": True
                        })
                        await asyncio.sleep(1.0)

            # Nếu gửi ảnh nhưng không kèm văn bản, tự động đặt lời nhắc mặc định
            if not prompt and image_paths:
                prompt = "Hãy phân tích và xử lý hình ảnh đính kèm này."

            # 2. Focus ô nhập và đưa văn bản lên khung chat IDE
            focus_expr = """
            (() => {
                const input = document.querySelector('[aria-label="Message input"]');
                if (!input) return false;
                input.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('delete', false, null);
                return true;
            })()
            """
            f_res = await cdp_call("Runtime.evaluate", {"expression": focus_expr, "returnByValue": True})
            if not f_res.get("result", {}).get("result", {}).get("value"):
                return False, "Không tìm thấy ô nhập tin nhắn Antigravity IDE"
                
            await asyncio.sleep(0.1)

            # Sử dụng CDP Input.insertText để văn bản hiện lên màn hình IDE như gõ phím thật
            if prompt:
                await cdp_call("Input.insertText", {"text": prompt})
                await asyncio.sleep(0.3)

            # 3. Kích hoạt gửi (Sent luôn)
            click_expr = """
            (() => {
                const btn = document.querySelector('button[data-testid="send-button"]') ||
                            document.querySelector('button[data-tooltip-id="input-send-button-send-tooltip"]') ||
                            document.querySelector('button[aria-label="Send message"]');
                if (btn && !btn.disabled) {
                    btn.click();
                    return { ok: true, method: "button_click" };
                }
                return { ok: false };
            })()
            """
            click_res = await cdp_call("Runtime.evaluate", {"expression": click_expr, "returnByValue": True})
            val = click_res.get("result", {}).get("result", {}).get("value")

            # Fallback: Nhấn Enter vật lý qua CDP nếu nút gửi chưa kích hoạt
            if not (isinstance(val, dict) and val.get("ok")):
                await cdp_call("Input.dispatchKeyEvent", {
                    "type": "rawKeyDown",
                    "windowsVirtualKeyCode": 13,
                    "unmodifiedText": "\r",
                    "text": "\r",
                    "key": "Enter",
                    "code": "Enter"
                })
                await cdp_call("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "windowsVirtualKeyCode": 13,
                    "key": "Enter",
                    "code": "Enter"
                })
                
            return True, None

    except Exception as e:
        logger.error(f"Error in inject_and_send: {e}", exc_info=True)
        return False, str(e)

async def wait_until_complete(chat_id, transcript_file, start_step, max_timeout=900):
    start_time = time.time()
    last_typing = 0
    poll_count = 0
    
    await asyncio.sleep(2.0)
    
    while time.time() - start_time < max_timeout:
        poll_count += 1
        now = time.time()
        if now - last_typing > 5.0:
            send_telegram_chat_action(chat_id, "typing")
            last_typing = now
            
        busy = await check_is_busy()
        ans = get_response_from_transcript(transcript_file, start_step)
        if ans and not busy:
            return ans
            
        if not busy and poll_count > 3:
            await asyncio.sleep(1.0)
            ans = get_response_from_transcript(transcript_file, start_step)
            if ans:
                return ans
            if poll_count > 5:
                return "✅ Antigravity đã hoàn tất tác vụ trên máy tính."
                
        await asyncio.sleep(1.5)
        
    return "⚠️ Quá thời gian chờ (timeout) 15 phút từ Antigravity."

# -------------------------------------------------------------
# Multi-command / Multi-Conversation Spawner
# -------------------------------------------------------------
def parse_multi_commands(text):
    """
    Phân tích chuỗi đầu vào thành nhiều lệnh độc lập.
    Hỗ trợ phân cách bởi:
    1. '---' hoặc '===' trên một dòng riêng.
    2. Đánh số thứ tự (ví dụ: '1. ... \n2. ...') nếu có từ 2 mục trở lên.
    """
    # 1. Thử tách theo phân cách khối
    lines = text.strip().splitlines()
    blocks = []
    current_block = []
    for line in lines:
        stripped = line.strip()
        if stripped in ["---", "===", "----", "===="] or (stripped.startswith("---") and len(stripped) >= 3 and set(stripped) == {'-'}):
            if current_block:
                blocks.append("\n".join(current_block).strip())
                current_block = []
        else:
            current_block.append(line)
    if current_block:
        blocks.append("\n".join(current_block).strip())
    blocks = [b for b in blocks if b]
    if len(blocks) >= 2:
        return blocks

    # 2. Thử tách theo danh sách đánh số: 1. ... 2. ...
    num_blocks = re.split(r'(?:^|\n)\s*(?:\d+[\.\)]|\[\d+\])\s+', text.strip())
    num_blocks = [b.strip() for b in num_blocks if b.strip()]
    if len(num_blocks) >= 2:
        return num_blocks

    return [text.strip()]

async def execute_multi_conversations(chat_id, commands, msg_id):
    """
    Khởi tạo song song N đoạn hội thoại trên Antigravity IDE.
    Mỗi lệnh sẽ được gửi vào một phiên chat mới độc lập.
    """
    total = len(commands)
    send_telegram(
        chat_id,
        f"🚀 *BẮT ĐẦU MỞ SONG SONG {total} ĐOẠN HỘI THOẠI TRÊN ANTIGRAVITY IDE:*\n"
        f"Hệ thống sẽ lần lượt tạo từng phiên chat mới và kích hoạt tác vụ chạy ngầm.",
        msg_id
    )

    spawned = []
    for idx, cmd_text in enumerate(commands, 1):
        try:
            send_telegram_chat_action(chat_id, "typing")
            # 1. Mở cửa sổ chat mới trên IDE
            await new_conversation()
            await asyncio.sleep(1.8)

            # 2. Bơm lệnh vào ô chat và gửi
            ok, err = await inject_and_send(cmd_text)
            if not ok:
                send_telegram(chat_id, f"❌ [{idx}/{total}] Lỗi khi gửi lệnh vào IDE: {err}", msg_id)
                continue

            await asyncio.sleep(1.8)
            new_cid = await get_current_conversation_id()
            short_cmd = cmd_text.replace("\n", " ")[:60]
            if len(cmd_text) > 60:
                short_cmd += "..."

            spawned.append({
                "index": idx,
                "cid": new_cid or "Chưa tạo xong",
                "title": short_cmd
            })

            send_telegram(
                chat_id,
                f"✅ *[{idx}/{total}] Đã mở đoạn chat mới:*\n"
                f"• *Nhiệm vụ:* _{short_cmd}_\n"
                f"• *ID:* `{new_cid or 'Khởi tạo...'}`",
                msg_id
            )
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Error spawning conversation {idx}: {e}")
            send_telegram(chat_id, f"⚠️ [{idx}/{total}] Gặp lỗi: {e}", msg_id)

    # Báo cáo tổng kết
    summary_lines = [
        f"🎉 *ĐÃ KHỞI TẠO XONG {len(spawned)}/{total} ĐOẠN HỘI THOẠI TRÊN IDE!*\n",
        "Tất cả các tác vụ hiện đang được Antigravity xử lý đồng thời trong nền.\n"
    ]
    for s in spawned:
        summary_lines.append(f"• *#{s['index']}*: `{s['title']}` (ID: `{s['cid'][:8] if s['cid'] else '...'}`)")

    summary_lines.append(
        "\n👉 *Mẹo*: Gõ `/chats` bất kỳ lúc nào để xem danh sách hoặc bấm nút chuyển đổi giữa các đoạn chat trên điện thoại!"
    )
    send_telegram(chat_id, "\n".join(summary_lines), msg_id)

# -------------------------------------------------------------
# Dynamic Top 5 Projects & Command Registration
# -------------------------------------------------------------
def clean_command_slug(text):
    """Chuẩn hóa tên lệnh theo chuẩn Telegram: chỉ chấp nhận a-z, 0-9 và dấu gạch dưới."""
    nfkd = unicodedata.normalize('NFKD', str(text))
    ascii_text = nfkd.encode('ASCII', 'ignore').decode('utf-8')
    cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', ascii_text).strip('_').lower()
    cleaned = re.sub(r'_+', '_', cleaned)
    return cleaned or "proj"

def get_top_5_projects():
    projects_map, _ = get_db_projects_and_conversations()
    recent_list = []
    seen = set()
    for name, p in projects_map.items():
        p_path = p.get("path", "")
        if p_path and p_path not in seen:
            seen.add(p_path)
            recent_list.append(p)
            if len(recent_list) >= 5:
                break
    return recent_list

def register_telegram_commands():
    try:
        url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/setMyCommands"
        top5 = get_top_5_projects()
        commands = []
        for idx, p in enumerate(top5, 1):
            slug = clean_command_slug(p.get("name", ""))[:15]
            cmd_name = f"p{idx}_{slug}"[:32]
            commands.append({
                "command": cmd_name,
                "description": f"📁 [{idx}/5] Dự án {p['name'][:25]}"
            })
            
        commands.extend([
            {"command": "projects", "description": "📂 Danh sách tất cả dự án trên IDE"},
            {"command": "chats", "description": "💬 Danh sách & chọn đoạn chat gần đây"},
            {"command": "multi", "description": "⚡ Mở nhiều đoạn chat song song cùng lúc"},
            {"command": "screen", "description": "🖥️ Chụp màn hình Desktop PC thực tế"},
            {"command": "sysinfo", "description": "💻 Xem CPU, RAM, Ổ cứng, Uptime PC"},
            {"command": "lock", "description": "🔒 Khóa màn hình máy tính từ xa"},
            {"command": "cmd", "description": "⚡ Chạy lệnh PowerShell trên PC"},
            {"command": "current", "description": "🔍 Xem dự án & đoạn chat đang mở"},
            {"command": "new", "description": "🆕 Mở đoạn chat mới trên IDE"},
            {"command": "stop", "description": "⏹️ Dừng khẩn cấp tác vụ đang chạy"},
            {"command": "help", "description": "📖 Hướng dẫn sử dụng & phím tắt"}
        ])
        
        payload = json.dumps({"commands": commands}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        logger.info(f"Successfully registered {len(commands)} Telegram bot commands.")
    except Exception as e:
        logger.warning(f"Failed to register Telegram commands: {e}")

# -------------------------------------------------------------
# Worker Queue Loop
# -------------------------------------------------------------
async def worker_loop():
    logger.info("Worker queue loop started.")
    while True:
        chat_id, text, image_path, msg_id = await message_queue.get()
        try:
            raw = text.strip()
            cmd = raw.lower()

            # --- 1. Lệnh /start ---
            if cmd == "/start":
                welcome = (
                    "👋 **Chào mừng bạn đến với Antigravity Direct Bridge!**\n\n"
                    "✨ Kết nối **trực tiếp 100%** vào Antigravity IDE & Hệ thống PC từ xa.\n\n"
                    "📌 **Điều khiển Antigravity IDE:**\n"
                    "• `/projects`: Danh sách Dự án & chọn đoạn chat\n"
                    "• `/chats`: Danh sách đoạn chat gần đây (chuyển 1 chạm)\n"
                    "• `/multi`: Mở nhiều đoạn chat song song cùng lúc\n"
                    "• `/current`: Xem dự án & đoạn chat đang mở trên IDE\n"
                    "• `/new`: Mở đoạn chat mới trên Antigravity\n"
                    "• `/stop`: Dừng khẩn cấp tác vụ đang chạy trên IDE\n\n"
                    "🖥️ **Quản lý máy tính (PC Remote Control):**\n"
                    "• `/screen`: Chụp ảnh màn hình Desktop PC gửi về Telegram\n"
                    "• `/sysinfo`: Xem CPU, RAM, Ổ cứng, Uptime, IP của PC\n"
                    "• `/lock`: Khóa màn hình máy tính Windows từ xa\n"
                    "• `/cmd <lệnh>`: Chạy lệnh PowerShell trực tiếp trên PC\n\n"
                    "📸 **Gửi ảnh trực tiếp**: Chụp hoặc gửi ảnh bất kỳ, bot sẽ nạp ảnh ngay vào IDE kèm lời nhắn!"
                )
                send_telegram(chat_id, welcome, msg_id)
                continue

            # --- 2. Lệnh /help ---
            if cmd in ["/help", "help", "/huongdan", "huongdan", "hướng dẫn", "huong dan"]:
                help_text = (
                    "📖 **HƯỚNG DẪN ĐIỀU KHIỂN ANTIGRAVITY & PC QUA TELEGRAM**\n\n"
                    "⚡ *Mẹo*: Khi gõ dấu `/`, Telegram sẽ tự động hiện danh sách lệnh và dự án để chọn ngay!\n\n"
                    "🛠️ **1. Điều khiển IDE:**\n"
                    "• `/projects`: Xem danh sách tất cả các dự án\n"
                    "• `/chats`: Xem tất cả đoạn chat gần đây\n"
                    "• `/multi`: Mở nhiều đoạn chat song song cùng lúc\n"
                    "  _Cú pháp_: Nhập các lệnh cách nhau bởi dòng `---`\n"
                    "• `/current`: Xem đoạn chat & dự án đang mở\n"
                    "• `/new`: Mở phiên làm việc mới trên IDE\n"
                    "• `/stop`: Dừng khẩn cấp lệnh đang chạy trên PC\n\n"
                    "💻 **2. Quản lý phần cứng PC:**\n"
                    "• `/screen`: Chụp màn hình Desktop thực tế\n"
                    "• `/sysinfo`: Báo cáo CPU, RAM, Ổ đĩa, Uptime\n"
                    "• `/lock`: Khóa máy tính tức thì\n"
                    "• `/cmd <lệnh>`: Thực thi lệnh PowerShell nhanh\n\n"
                    "📸 **Gửi ảnh**: Gửi ảnh bất kỳ để nạp thẳng vào IDE làm việc!"
                )
                send_telegram(chat_id, help_text, msg_id)
                continue

            # --- 3. Lệnh /screen (Chụp màn hình Desktop) ---
            if cmd in ["/screen", "/screenshot", "screen", "screenshot", "chụp màn hình", "chupmanhinh", "/chupmanhinh"]:
                send_telegram_chat_action(chat_id, "upload_photo")
                img_path = os.path.join(config.RECEIVED_IMAGES_DIR, f"screen_{int(time.time())}.jpg")
                try:
                    w, h = capture_screen_gdi(img_path)
                    now_str = datetime.datetime.now().strftime("%H:%M:%S %d/%m/%Y")
                    caption = f"🖥️ *Ảnh chụp màn hình PC*\n• *Thời gian:* `{now_str}`\n• *Độ phân giải:* `{w}x{h}`"
                    send_telegram_photo(chat_id, img_path, caption, msg_id)
                except Exception as e:
                    logger.error(f"Screenshot error: {e}", exc_info=True)
                    send_telegram(chat_id, f"❌ Lỗi khi chụp màn hình PC: {e}", msg_id)
                continue

            # --- 4. Lệnh /sysinfo (Thông số phần cứng) ---
            if cmd in ["/sysinfo", "/pc", "sysinfo", "pc", "thông số", "thong so", "/status_pc", "cấu hình", "cau hinh"]:
                send_telegram_chat_action(chat_id, "typing")
                try:
                    report = get_system_hardware_info()
                    send_telegram(chat_id, report, msg_id)
                except Exception as e:
                    send_telegram(chat_id, f"❌ Lỗi lấy thông số PC: {e}", msg_id)
                continue

            # --- 5. Lệnh /lock (Khóa màn hình máy tính) ---
            if cmd in ["/lock", "lock", "khóa máy", "khoa may", "/khoa"]:
                ok = lock_windows_workstation()
                if ok:
                    send_telegram(chat_id, "🔒 **ĐÃ KHÓA MÀN HÌNH MÁY TÍNH (WINDOWS LOCK) THÀNH CÔNG!**", msg_id)
                else:
                    send_telegram(chat_id, "⚠️ Không thể khóa màn hình máy tính.", msg_id)
                continue

            # --- 6. Lệnh /cmd <lệnh> (Chạy lệnh shell/powershell trực tiếp) ---
            if raw.startswith("/cmd ") or raw.startswith("/ps ") or raw.startswith("/terminal "):
                cmd_to_run = raw.split(" ", 1)[1].strip()
                if not cmd_to_run:
                    send_telegram(chat_id, "⚠️ Vui lòng nhập lệnh sau `/cmd`. Ví dụ: `/cmd Get-Process python`", msg_id)
                    continue
                send_telegram(chat_id, f"⚡ Đang chạy lệnh trên PC: `{cmd_to_run}`...", msg_id)
                send_telegram_chat_action(chat_id, "typing")
                res = execute_system_cmd(cmd_to_run)
                send_telegram(chat_id, res, msg_id)
                continue

            # --- 7. Lệnh /projects ---
            if cmd in ["/projects", "/project", "projects", "du an", "dự án", "/duan"]:
                await show_projects_menu(chat_id, msg_id)
                continue

            # --- 8. Lệnh /chats ---
            if cmd in ["/chats", "/tasks", "chats", "tasks", "doan chat", "đoạn chat"]:
                await show_all_chats_menu(chat_id, msg_id)
                continue

            # --- 9. Lệnh /stop ---
            if cmd in ["/stop", "stop", "dừng", "dung", "cancel", "/cancel"]:
                ok, err = await stop_task()
                if ok:
                    send_telegram(chat_id, "⏹️ **Đã gửi tín hiệu dừng khẩn cấp tác vụ trên Antigravity IDE!**", msg_id)
                else:
                    send_telegram(chat_id, f"⚠️ Không có tác vụ nào đang chạy hoặc không thể dừng: {err}", msg_id)
                continue

            # --- 10. Lệnh /new ---
            if cmd in ["/new", "new", "mới", "moi", "/newchat"]:
                ok, err = await new_conversation()
                if ok:
                    send_telegram(chat_id, "🆕 **Đã mở đoạn chat mới trên Antigravity IDE.**\nBây giờ bạn có thể gửi yêu cầu mới!", msg_id)
                else:
                    send_telegram(chat_id, f"⚠️ Lỗi khi mở đoạn chat mới: {err}", msg_id)
                continue

            # --- 11. Lệnh /current ---
            if cmd in ["/current", "/status", "status", "hien tai", "hiện tại"]:
                curr_cid = await get_current_conversation_id()
                active = get_active_project()
                _, all_convs = get_db_projects_and_conversations()
                curr_conv = next((c for c in all_convs if curr_cid and c["id"].startswith(curr_cid[:8])), None)
                
                title = curr_conv['title'] if curr_conv else "Chưa xác định"
                msg = (
                    f"🔍 **TRẠNG THÁI HIỆN TẠI TRÊN IDE:**\n\n"
                    f"• *Dự án:* `{active.get('name')}`\n"
                    f"• *Thư mục:* `{active.get('path')}`\n"
                    f"• *Đoạn chat:* *{title}*\n"
                    f"• *ID:* `{curr_cid or 'Chưa mở đoạn chat'}`"
                )
                send_telegram(chat_id, msg, msg_id)
                continue

            # --- 12. Điều hướng số (1, 2, 3...) khi đang xem Menu ---
            if raw.isdigit() and chat_id in user_nav_context:
                idx = int(raw) - 1
                ctx = user_nav_context[chat_id]
                items = ctx.get("items", [])
                if 0 <= idx < len(items):
                    item = items[idx]
                    if ctx["mode"] == "projects":
                        await show_project_chats_menu(chat_id, item, msg_id)
                        continue
                    elif ctx["mode"] in ["project_chats", "all_chats"]:
                        await do_switch_conversation(chat_id, item, msg_id)
                        continue

            # --- 13. Xử lý MỞ NHIỀU ĐOẠN HỘI THOẠI SONG SONG (/multi hoặc phân cách ---) ---
            is_explicit_multi = raw.startswith("/multi") or raw.startswith("/spawn") or raw.startswith("/batch")
            sub_text = raw
            if is_explicit_multi:
                # Bỏ prefix lệnh
                parts = raw.split("\n", 1)
                sub_text = parts[1].strip() if len(parts) > 1 else ""

            commands_list = parse_multi_commands(sub_text) if sub_text else []
            if (is_explicit_multi and commands_list) or len(commands_list) >= 2:
                if len(commands_list) == 1 and not is_explicit_multi:
                    pass
                else:
                    await execute_multi_conversations(chat_id, commands_list, msg_id)
                    continue

            # --- 14. Gửi lệnh / prompt đơn lẻ trực tiếp vào đoạn chat hiện tại trên IDE ---
            curr_cid = await get_current_conversation_id()
            t_file, last_step = get_latest_transcript_info(curr_cid)
            
            send_telegram_chat_action(chat_id, "typing")
            
            imgs = [image_path] if image_path else None
            ok, err = await inject_and_send(raw, imgs)
            if not ok:
                send_telegram(chat_id, f"❌ Lỗi gửi lệnh vào Antigravity: {err}", msg_id)
                continue
                
            resp_text = await wait_until_complete(chat_id, t_file, last_step)
            send_telegram(chat_id, resp_text, msg_id)

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            send_telegram(chat_id, f"❌ Có lỗi xảy ra: {e}", msg_id)
        finally:
            message_queue.task_done()

# -------------------------------------------------------------
# Main Polling Loop
# -------------------------------------------------------------
async def main():
    logger.info("=== Antigravity Direct Telegram Bridge Started ===")
    
    # 0. Đảm bảo chỉ 1 tiến trình duy nhất chạy
    ensure_single_instance()
    
    # 1. Chờ kết nối mạng (Internet) sẵn sàng
    wait_for_internet()
    
    # 2. Đảm bảo Google Antigravity IDE đang chạy
    ensure_antigravity_running()
    
    # 3. Detect CDP Port & Tab
    cdp_url = detect_cdp_url()
    tab = get_active_tab()
    if tab:
        logger.info(f"Connected to Antigravity at {cdp_url}: {tab.get('title')}")
    else:
        logger.warning(f"Could not connect to Antigravity at {cdp_url}. Please ensure Antigravity IDE is running.")
        
    # 4. Register Telegram slash commands
    register_telegram_commands()
    
    # 5. Gửi thông báo chào mừng & báo cáo sẵn sàng đến Telegram của Đại Ca
    active_port = get_devtools_active_port()
    notify_startup(active_port)
    
    # 6. Start worker
    asyncio.create_task(worker_loop())
    
    # 7. Long-polling
    offset = None
    poll_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/getUpdates"
    
    # Drop pending updates
    try:
        drop_req = urllib.request.urlopen(f"{poll_url}?offset=-1", timeout=10)
        drop_data = json.loads(drop_req.read().decode())
        if drop_data.get("result"):
            offset = drop_data["result"][-1]["update_id"] + 1
            logger.info(f"Dropped pending updates, starting offset: {offset}")
    except Exception as e:
        logger.warning(f"Error dropping pending updates: {e}")

    while True:
        try:
            params = {"timeout": 25}
            if offset:
                params["offset"] = offset
            q = urllib.parse.urlencode(params)
            req = urllib.request.Request(f"{poll_url}?{q}")
            
            resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=35)
            data = json.loads(resp.read().decode())
            
            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    
                    # Handle Callback Query (Buttons)
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        cb_id = cb["id"]
                        cb_from = cb.get("from", {}).get("id")
                        cb_data = cb.get("data", "")
                        cb_msg_id = cb.get("message", {}).get("message_id")
                        chat_id = cb.get("message", {}).get("chat", {}).get("id")
                        
                        if config.ALLOWED_USER_IDS and cb_from not in config.ALLOWED_USER_IDS:
                            answer_callback_query(cb_id, "⚠️ Bạn không có quyền điều khiển bot này.", show_alert=True)
                            continue
                            
                        answer_callback_query(cb_id)
                        await handle_callback_query(chat_id, cb_data, cb_msg_id)
                        continue

                    # Handle Regular Messages
                    msg = update.get("message")
                    if not msg:
                        continue
                        
                    from_user = msg.get("from", {})
                    user_id = from_user.get("id")
                    chat_id = msg.get("chat", {}).get("id")
                    msg_id = msg.get("message_id")
                    
                    if config.ALLOWED_USER_IDS and user_id not in config.ALLOWED_USER_IDS:
                        logger.warning(f"Unauthorized access attempt from user ID: {user_id}")
                        continue
                        
                    text = msg.get("text") or msg.get("caption") or ""
                    
                    # Check for Photo Attachment
                    image_path = None
                    if "photo" in msg:
                        photos = msg["photo"]
                        best_photo = photos[-1]
                        try:
                            image_path = download_telegram_file(best_photo["file_id"])
                        except Exception as e:
                            logger.error(f"Failed to download photo: {e}")
                            send_telegram(chat_id, f"⚠️ Không thể tải ảnh: {e}", msg_id)
                            
                    if text or image_path:
                        await message_queue.put((chat_id, text, image_path, msg_id))

        except asyncio.CancelledError:
            break
        except urllib.error.HTTPError as he:
            if he.code == 409:
                logger.error("HTTP 409 Conflict: Bot token này đang chạy ở một cửa sổ hoặc máy tính khác! Vui lòng chỉ mở 1 bot tại 1 thời điểm.")
                await asyncio.sleep(6)
            else:
                logger.error(f"Polling loop HTTP error ({he.code}): {he}")
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Polling loop error: {e}")
            if "getaddrinfo" in str(e).lower() or "network" in str(e).lower() or "timed out" in str(e).lower():
                wait_for_internet(timeout=180, check_interval=5)
            await asyncio.sleep(2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
