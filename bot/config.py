import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.isdigit()]
DB_URL = os.getenv("DB_URL", "sqlite+aiosqlite:///./message_bot.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
TG_WORKER_RESTART_DELAY = int(os.getenv("TG_WORKER_RESTART_DELAY", "10"))
TG_SYNC_INTERVAL = int(os.getenv("TG_SYNC_INTERVAL", "30"))
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "20"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))

SESSIONS_DIR = "sessions"
