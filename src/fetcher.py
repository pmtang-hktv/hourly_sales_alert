from __future__ import annotations

import email
import imaplib
import logging
from datetime import datetime, timedelta

from src.config import IMAP_FOLDER, IMAP_HOST, IMAP_PASS, IMAP_PORT, IMAP_USER

log = logging.getLogger(__name__)

SUBJECT_KEYWORD = "HKTVmall Payment Gateway Report"


def _connect() -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(IMAP_USER, IMAP_PASS)
    mail.select(IMAP_FOLDER)
    return mail


def fetch_emails_since(days: int = 1) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    mail = _connect()
    try:
        _, data = mail.search(None, f'(SINCE "{since}" SUBJECT "{SUBJECT_KEYWORD}")')
        ids = data[0].split()
        log.info("Found %d matching emails since %s", len(ids), since)

        results = []
        for uid in ids:
            _, msg_data = mail.fetch(uid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            results.append({
                "subject": msg.get("Subject", ""),
                "date": msg.get("Date", ""),
                "body": _extract_body(msg),
            })
        return results
    finally:
        mail.logout()


def fetch_latest_email() -> dict | None:
    """Return the most recent payment gateway report email."""
    emails = fetch_emails_since(days=1)
    if not emails:
        emails = fetch_emails_since(days=2)
    return emails[-1] if emails else None


def _extract_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                break
            elif ct == "text/html" and not body:
                html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                body = _html_to_text(html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="ignore")
            if msg.get_content_type() == "text/html":
                body = _html_to_text(body)
    return body


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            rows.append("\t".join(cells))
        table.replace_with("\n".join(rows))
    return soup.get_text(separator="\n")
