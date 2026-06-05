import email as emaillib
import imaplib
from datetime import datetime, timedelta

from src.config import IMAP_HOST, IMAP_PASS, IMAP_PORT, IMAP_USER

since = (datetime.now() - timedelta(days=2)).strftime("%d-%b-%Y")
mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
mail.sock.settimeout(60)
mail.login(IMAP_USER, IMAP_PASS)

# First list all folders to find the correct name
print("Available folders:")
_, folders = mail.list()
for f in folders:
    print(" ", f.decode() if isinstance(f, bytes) else f)
print()

# Try different folder name formats
selected = None
for folder in ['"BI - Daily Sales Update"', 'BI - Daily Sales Update', '"BI/BI - Daily Sales Update"', 'BI/BI - Daily Sales Update']:
    try:
        status, data = mail.select(folder)
        print(f"select({folder!r}) -> {status} {data}")
        if status == "OK":
            selected = folder
            break
    except Exception as e:
        print(f"select({folder!r}) -> ERROR: {e}")

if not selected:
    print("All folder formats failed — check folder names above")
    mail.logout()
    exit()

_, data = mail.search(None, f'(SINCE "{since}" SUBJECT "Daily Sales Update")')
ids = data[0].split()
print(f"\nFound {len(ids)} emails — listing all subjects:")
for uid in ids:
    _, md = mail.fetch(uid, "(BODY[HEADER.FIELDS (SUBJECT)])")
    subj = md[0][1].decode(errors="ignore").strip()
    print(f"  uid={uid.decode()}: {subj}")
mail.logout()
