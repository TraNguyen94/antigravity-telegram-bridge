import asyncio
import glob
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
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
    ext = os.path.splitext(remote_path)[1] or ".jpg"
    filename = f"tg_{int(time.time())}_{os.path.basename(remote_path)}"
    local_path = os.path.join(dest_dir, filename)
    
    download_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{remote_path}"
    req_dl = urllib.request.Request(download_url)
    with urllib.request.urlopen(req_dl, timeout=30) as r, open(local_path, "wb") as f:
        f.write(r.read())
    logger.info(f"Downloaded Telegram photo to {local_path} ({os.path.getsize(local_path)} bytes)")
    return local_path

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
            req = urllib.request.Request(url, timeout=1.5)
            tabs = json.loads(req.read().decode())
            if isinstance(tabs, list) and any("webSocketDebuggerUrl" in t for t in tabs):
                CDP_LIST_URL = url
                logger.info(f"Connected to Antigravity port via DevToolsActivePort: {active_port}")
                return url
        except Exception:
            pass

    # 2. Thử các cổng quen thuộc
    cached_ports = [config.DEFAULT_CDP_PORT, 57061, 50717, 9222]
    for port in cached_ports:
        try:
            url = f"http://127.0.0.1:{port}/json/list"
            req = urllib.request.Request(url, timeout=1)
            tabs = json.loads(req.read().decode())
            if isinstance(tabs, list) and any("webSocketDebuggerUrl" in t for t in tabs):
                CDP_LIST_URL = url
                return url
        except Exception:
            pass

    # 3. Quét mạng trên Windows nếu cần
    if sys.platform == "win32":
        try:
            import subprocess
            out = subprocess.check_output(
                'powershell -NoProfile -Command "Get-NetTCPConnection -State Listen | Where-Object { $_.LocalAddress -eq \'127.0.0.1\' } | Select-Object -ExpandProperty LocalPort"',
                shell=True, text=True, timeout=5
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
                    req = urllib.request.Request(url, timeout=0.8)
                    tabs = json.loads(req.read().decode())
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
        const btn = document.querySelector('button[aria-label="New Conversation"]');
        if (btn) {
            btn.click();
            return true;
        }
        return false;
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
                        dt = datetime.strptime(dt_part, "%Y-%m-%d %H:%M:%S")
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
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            # Thử click nút Gửi của Antigravity (data-tooltip-id chứa input-send-button)
            click_expr = """
            (() => {
                const btn = document.querySelector('button[data-tooltip-id*="input-send-button"]:not([aria-label*="Cancel"])') ||
                            document.querySelector('button[aria-label="Send message"]') ||
                            document.querySelector('button[data-tooltip-id*="send"]') ||
                            document.querySelector('button[type="submit"]');
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
                    "✨ Kết nối **trực tiếp 100%** vào Antigravity IDE trên máy tính.\n\n"
                    "📌 **Điều khiển Dự án & Đoạn chat:**\n"
                    "• `/projects`: Xem danh sách tất cả Dự án & chọn đoạn chat\n"
                    "• `/chats`: Xem nhanh tất cả đoạn chat gần đây (chuyển đổi 1 chạm)\n"
                    "• `/current`: Xem đoạn chat & dự án đang mở trên IDE\n"
                    "• `/new`: Mở đoạn chat mới trên Antigravity\n"
                    "• `/stop`: Dừng khẩn cấp tác vụ đang chạy trên PC\n\n"
                    "📸 **Gửi ảnh trực tiếp**: Chụp hoặc gửi ảnh bất kỳ, bot sẽ nạp ảnh ngay vào IDE kèm lời nhắn!"
                )
                send_telegram(chat_id, welcome, msg_id)
                continue

            # --- 2. Lệnh /help ---
            if cmd in ["/help", "help", "/huongdan", "huongdan", "hướng dẫn", "huong dan"]:
                help_text = (
                    "📖 **HƯỚNG DẪN ĐIỀU KHIỂN ANTIGRAVITY IDE QUA TELEGRAM**\n\n"
                    "⚡ *Mẹo*: Khi gõ dấu `/`, Telegram sẽ tự động hiện danh sách dự án gần nhất để bạn chọn ngay!\n\n"
                    "🛠️ **Lệnh điều khiển chung:**\n"
                    "• `/projects`: Xem danh sách tất cả các dự án trên máy tính\n"
                    "• `/chats`: Xem tất cả đoạn chat gần đây kèm tag dự án\n"
                    "• `/current`: Xem màn hình & đoạn chat đang mở trên PC\n"
                    "• `/new`: Mở phiên làm việc mới trên Antigravity IDE\n"
                    "• `/stop`: Dừng khẩn cấp lệnh đang chạy trên PC\n"
                    "📸 **Gửi ảnh**: Gửi ảnh bất kỳ để nạp thẳng vào IDE làm việc!"
                )
                send_telegram(chat_id, help_text, msg_id)
                continue

            # --- 3. Lệnh /projects ---
            if cmd in ["/projects", "/project", "projects", "du an", "dự án", "/duan"]:
                await show_projects_menu(chat_id, msg_id)
                continue

            # --- 4. Lệnh /chats ---
            if cmd in ["/chats", "/tasks", "chats", "tasks", "doan chat", "đoạn chat"]:
                await show_all_chats_menu(chat_id, msg_id)
                continue

            # --- 5. Lệnh /stop ---
            if cmd in ["/stop", "stop", "dừng", "dung", "cancel", "/cancel"]:
                ok, err = await stop_task()
                if ok:
                    send_telegram(chat_id, "⏹️ **Đã gửi tín hiệu dừng khẩn cấp tác vụ trên Antigravity IDE!**", msg_id)
                else:
                    send_telegram(chat_id, f"⚠️ Không có tác vụ nào đang chạy hoặc không thể dừng: {err}", msg_id)
                continue

            # --- 6. Lệnh /new ---
            if cmd in ["/new", "new", "mới", "moi", "/newchat"]:
                ok, err = await new_conversation()
                if ok:
                    send_telegram(chat_id, "🆕 **Đã mở đoạn chat mới trên Antigravity IDE.**\nBây giờ bạn có thể gửi yêu cầu mới!", msg_id)
                else:
                    send_telegram(chat_id, f"⚠️ Lỗi khi mở đoạn chat mới: {err}", msg_id)
                continue

            # --- 7. Lệnh /current ---
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

            # --- 8. Điều hướng số (1, 2, 3...) khi đang xem Menu ---
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

            # --- 9. Gửi lệnh / prompt trực tiếp vào Antigravity IDE ---
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
    
    # 1. Detect CDP Port & Tab
    cdp_url = detect_cdp_url()
    tab = get_active_tab()
    if tab:
        logger.info(f"Connected to Antigravity at {cdp_url}: {tab.get('title')}")
    else:
        logger.warning(f"Could not connect to Antigravity at {cdp_url}. Please ensure Antigravity IDE is running.")
        
    # 2. Register Telegram slash commands
    register_telegram_commands()
    
    # 3. Start worker
    asyncio.create_task(worker_loop())
    
    # 4. Long-polling
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
            await asyncio.sleep(2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
