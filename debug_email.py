import email as emaillib
import imaplib
from datetime import datetime, timedelta

from src.config import IMAP_HOST, IMAP_PASS, IMAP_PORT, IMAP_USER

since = (datetime.now() - timedelta(days=2)).strftime("%d-%b-%Y")
mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
mail.sock.settimeout(60)
mail.login(IMAP_USER, IMAP_PASS)
mail.select('"BI - Daily Sales Update"')
_, data = mail.search(None, f'(SINCE "{since}" SUBJECT "Daily Sales Update")')
ids = data[0].split()
print(f"Found {len(ids)} emails")
_, msg_data = mail.fetch(ids[-1], "(RFC822)")
msg = emaillib.message_from_bytes(msg_data[0][1])
print("Subject:", msg.get("Subject"))
print()
print("All image parts:")
for i, part in enumerate(msg.walk()):
    ct = part.get_content_type()
    if "image" in ct or ct == "application/octet-stream":
        payload = part.get_payload(decode=True)
        size = len(payload) if payload else 0
        print(f"  [{i}] type={ct} size={size:,} bytes filename={part.get_filename()}")
mail.logout()
