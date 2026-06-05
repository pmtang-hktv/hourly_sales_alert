from __future__ import annotations

import email
import imaplib
import logging
from datetime import datetime, timedelta

from src.config import IMAP_DAILY_FOLDER, IMAP_HOST, IMAP_PASS, IMAP_PORT, IMAP_USER

log = logging.getLogger(__name__)

SUBJECT_KEYWORD = "Daily Sales Update"
# Exact target after stripping "FW:" prefix — the full platform dashboard email
TARGET_SUBJECT = "Daily Sales Update (Basic)"
EXCLUDE_SUBJECT = "Offline Only"
IMAP_TIMEOUT = 60


def _connect() -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.sock.settimeout(IMAP_TIMEOUT)
    mail.login(IMAP_USER, IMAP_PASS)
    mail.select(f'"{IMAP_DAILY_FOLDER}"')
    return mail


def fetch_daily_dashboard_email() -> dict | None:
    """Return the most recent Daily Sales Update email with its image attachment."""
    since = (datetime.now() - timedelta(days=2)).strftime("%d-%b-%Y")
    try:
        mail = _connect()
    except Exception as exc:
        log.error("Failed to connect to daily folder: %s", exc)
        return None

    try:
        _, data = mail.search(None, f'(SINCE "{since}" SUBJECT "{SUBJECT_KEYWORD}")')
        ids = data[0].split()
        if not ids:
            log.warning("No daily dashboard email found since %s", since)
            return None

        # Filter to the exact "(Basic)" platform dashboard, excluding "Offline Only".
        # Iterate newest-first and take the first match.
        target_uid = None
        target_subject = ""
        for uid in reversed(ids):
            _, md = mail.fetch(uid, "(BODY[HEADER.FIELDS (SUBJECT)])")
            subj = md[0][1].decode(errors="ignore")
            if TARGET_SUBJECT in subj and EXCLUDE_SUBJECT not in subj:
                target_uid = uid
                target_subject = subj.replace("Subject:", "").strip()
                break

        if not target_uid:
            log.warning("No '%s' email found (excluding '%s')", TARGET_SUBJECT, EXCLUDE_SUBJECT)
            return None

        log.info("Matched daily dashboard email: %s", target_subject)
        _, msg_data = mail.fetch(target_uid, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        image_bytes = _extract_image(msg)
        if not image_bytes:
            log.warning("No image attachment found in daily dashboard email")
            return None

        return {
            "subject": msg.get("Subject", ""),
            "date": msg.get("Date", ""),
            "image_bytes": image_bytes,
        }
    except Exception as exc:
        log.error("Error fetching daily dashboard email: %s", exc)
        return None
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _extract_image(msg) -> bytes | None:
    """Extract the first image attachment (or inline image) from the email."""
    for part in msg.walk():
        ct = part.get_content_type()
        disposition = part.get("Content-Disposition", "")
        if ct.startswith("image/") or (
            ct in ("application/octet-stream",) and "attachment" in disposition
        ):
            payload = part.get_payload(decode=True)
            if payload:
                log.info("Extracted image attachment (%d bytes, type=%s)", len(payload), ct)
                return payload
    return None
