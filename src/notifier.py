import logging

import requests

from src.config import TELEGRAM_ALL_CHAT_IDS, TELEGRAM_BOT_TOKEN

log = logging.getLogger(__name__)


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALL_CHAT_IDS:
        log.warning("Telegram not configured — skipping send")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    success = False
    for chat_id in TELEGRAM_ALL_CHAT_IDS:
        try:
            resp = requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
            resp.raise_for_status()
            success = True
        except requests.RequestException as e:
            log.error("Failed to send Telegram message to %s: %s", chat_id, e)
    if success:
        log.info("Telegram message sent (%d chars) to %d recipients", len(message), len(TELEGRAM_ALL_CHAT_IDS))
    return success
