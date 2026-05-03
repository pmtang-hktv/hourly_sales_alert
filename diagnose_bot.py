"""Quick diagnostic — run this while main.py is NOT running."""
import os, requests
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

print(f"Token set:   {bool(TOKEN)}")
print(f"Chat ID:     '{CHAT_ID}'")

# 1. Check bot identity
r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=10)
print(f"\nBot info: {r.json()}")

# 2. Check pending updates
r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=10)
updates = r.json().get("result", [])
print(f"\nPending updates: {len(updates)}")
for u in updates[-3:]:
    msg = u.get("message", {})
    incoming_chat = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "")
    match = incoming_chat == str(CHAT_ID)
    print(f"  update_id={u['update_id']}  chat_id={incoming_chat}  match={match}  text='{text}'")

if not updates:
    print("  (no updates — send a message to your bot first, then re-run)")
