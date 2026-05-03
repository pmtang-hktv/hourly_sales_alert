"""
One-time script to backfill the past 30 days of historical email data.
Run this once before starting main.py so the anomaly detector has enough history.

Usage:
    python backfill.py
"""
import logging

from src.db import init_db, upsert_daily, upsert_hourly
from src.fetcher import fetch_emails_since
from src.parser import parse_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    init_db()
    log.info("Fetching emails from the past 32 days...")
    emails = fetch_emails_since(days=32)
    log.info("Found %d emails", len(emails))

    success = 0
    for raw in emails:
        result = parse_email(raw["subject"], raw["body"])
        if not result:
            log.warning("Could not parse: %s", raw.get("subject", "(no subject)"))
            continue

        for row in result["rows"]:
            upsert_hourly(row)

        if result["email_hour"] == 0 and result["summary"]:
            upsert_daily({"report_date": result["report_date"], **result["summary"]})

        log.info(
            "Stored %s  hour=%02d  rows=%d",
            result["report_date"],
            result["email_hour"],
            len(result["rows"]),
        )
        success += 1

    log.info("Backfill complete: %d / %d emails stored", success, len(emails))


if __name__ == "__main__":
    main()
