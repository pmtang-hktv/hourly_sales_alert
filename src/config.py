import os
from dotenv import load_dotenv

load_dotenv()

IMAP_HOST = os.getenv("IMAP_HOST", "zmail.hktv.com.hk")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASS = os.getenv("IMAP_PASS", "")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
IMAP_DAILY_FOLDER = os.getenv("IMAP_DAILY_FOLDER", "BI - Daily Sales Update")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_extra = [x.strip() for x in os.getenv("TELEGRAM_EXTRA_CHAT_IDS", "").split(",") if x.strip()]
TELEGRAM_ALL_CHAT_IDS: list = ([TELEGRAM_CHAT_ID] if TELEGRAM_CHAT_ID else []) + _extra

DB_PATH = os.getenv("DB_PATH", "data/sales.db")
# Folder where the daily Tableau Category Performance xlsx is dropped/downloaded
CATEGORY_DIR = os.getenv("CATEGORY_DIR", "data/category")
ANOMALY_Z_THRESHOLD = float(os.getenv("ANOMALY_Z_THRESHOLD", "2.0"))
MIN_GATEWAY_VOLUME = int(os.getenv("MIN_GATEWAY_VOLUME", "20"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Tableau Server automation
TABLEAU_SERVER = os.getenv("TABLEAU_SERVER", "https://inhouse-analytics.hktv.com.hk")
TABLEAU_USERNAME = os.getenv("TABLEAU_USERNAME", "")
TABLEAU_PASSWORD = os.getenv("TABLEAU_PASSWORD", "")
TABLEAU_ACCESS_KEY = os.getenv("TABLEAU_ACCESS_KEY", "")
TABLEAU_HEADLESS = os.getenv("TABLEAU_HEADLESS", "true").lower() not in ("false", "0", "no")
