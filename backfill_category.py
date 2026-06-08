"""Backfill historical Category Performance data from Tableau.

Usage:
  python3 backfill_category.py [DAYS]

  DAYS defaults to 5. Downloads the past DAYS days (ending yesterday),
  parses each file, stores rows in the DB, and prints a summary.
  Does NOT send Telegram messages.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

from src.category_parser import parse_category_file
from src.db import init_db, upsert_category_rows
from src.tableau_downloader import backfill_category_performance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill")


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    yesterday = (datetime.now() - timedelta(days=1)).date()
    dates = [yesterday - timedelta(days=i) for i in range(days)]  # newest first
    log.info("Backfilling %d days: %s … %s", days, dates[-1], dates[0])

    init_db()
    results = backfill_category_performance(dates)

    print("\n===== Backfill summary =====")
    ok = 0
    for d in dates:
        iso = d.strftime("%Y-%m-%d")
        path = results.get(iso)
        if not path:
            print(f"  {iso}: FAILED (no file)")
            continue
        try:
            rows = parse_category_file(path)
            upsert_category_rows(iso, rows)
            total_gmv = sum(r["gmv"] or 0 for r in rows)
            print(f"  {iso}: {len(rows)} rows, total GMV HKD {total_gmv:,.0f}")
            ok += 1
        except Exception as exc:
            print(f"  {iso}: parse/store error — {exc}")
    print(f"===== {ok}/{len(dates)} days stored =====")


if __name__ == "__main__":
    main()
