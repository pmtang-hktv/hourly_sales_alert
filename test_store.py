"""Quick test: send the store performance Telegram message for a given date.

Skips backfill — uses data already in the DB or parses the local xlsx file if present.

Usage:
  python3 test_store.py [YYYY-MM-DD]   # defaults to yesterday
"""
from __future__ import annotations

import glob
import logging
import os
import sys
from datetime import datetime, timedelta

from src.config import STORE_DIR
from src.db import init_db, upsert_store_rows, get_store_totals
from src.tableau_downloader import _parse_store_gmv_xlsx
from src.store_analyzer import build_store_summary
from src.reporter import format_store_summary
from src.notifier import send_telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("test_store")


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    init_db()

    # Check if data already exists in DB
    existing = get_store_totals(date)
    if existing:
        log.info("Using %d stores already in DB for %s", len(existing), date)
    else:
        # Try to parse from local xlsx
        files = glob.glob(os.path.join(STORE_DIR, f"*{date}*.xlsx"))
        if not files:
            print(f"No data in DB and no xlsx found in {STORE_DIR} for {date}.")
            print("Place the downloaded xlsx there or run a 1-day backfill first:")
            print("  python3 backfill_store.py 1")
            sys.exit(1)
        path = files[0]
        log.info("Parsing local file: %s", path)
        with open(path, "rb") as f:
            rows = _parse_store_gmv_xlsx(f.read())
        upsert_store_rows(date, rows)
        log.info("Stored %d rows, total GMV HKD %s", len(rows), f"{sum(r['gmv'] for r in rows):,.0f}")

    data = build_store_summary(date)
    msg = format_store_summary(data)

    print("\n" + "=" * 60)
    print(msg)
    print("=" * 60 + "\n")

    ans = input("Send this to Telegram? [y/N] ").strip().lower()
    if ans == "y":
        if send_telegram(msg):
            print("Sent.")
        else:
            print("Send failed — check logs.")
    else:
        print("Not sent.")


if __name__ == "__main__":
    main()
