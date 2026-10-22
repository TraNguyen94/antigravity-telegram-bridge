import os
from pathlib import Path

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Logging & state files
LOG_FILE = DATA_DIR / "bridge.log"
ACTIVE_PROJECT_FILE = DATA_DIR / "active_project.json"
RECEIVED_IMAGES_DIR = DATA_DIR / "received_images"
RECEIVED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Telegram Bot Credentials
BOT_TOKEN = os.getenv("BOT_TOKEN", "8884877895:AAFUpGHMul8wLTVIA7ioYZePYZAiROooPxk")

# Allowed Telegram User IDs (comma separated string -> set of ints)
raw_users = os.getenv("ALLOWED_USER_IDS", os.getenv("ALLOWED_USER_ID", "5889330916,8353584732"))
ALLOWED_USER_IDS = set()
for uid in str(raw_users).split(","):
    uid = uid.strip()
    if uid.isdigit():
        ALLOWED_USER_IDS.add(int(uid))

# Antigravity IDE Paths (Cross-Platform auto detection)
GEMINI_DIR = Path(os.getenv("GEMINI_DIR", Path.home() / ".gemini"))
BRAIN_DIR = Path(os.getenv("BRAIN_DIR", GEMINI_DIR / "antigravity" / "brain"))
SUMMARIES_DB_PATH = Path(os.getenv("SUMMARIES_DB_PATH", GEMINI_DIR / "antigravity" / "conversation_summaries.db"))

# Default / Fallback CDP Port
DEFAULT_CDP_PORT = int(os.getenv("CDP_PORT", "57061"))

# Custom Known Projects (Optional: configured in .env as semicolon or comma separated paths)
raw_known_paths = os.getenv("KNOWN_PROJECT_PATHS", "")
KNOWN_PROJECT_PATHS = [p.strip() for p in raw_known_paths.split(";") if p.strip()] if raw_known_paths else []
