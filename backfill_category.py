"""Backfill historical Category Performance (GMV) data from Tableau.

Usage:
  python3 backfill_category.py [DAYS]

  DAYS defaults to 5. Downloads the past DAYS days (ending yesterday),
  stores rows in the DB, and prints a summary. Does NOT send Telegram messages.

Strategy (auto-selected):
  1. REST API via PAT2 (MonthlySalesbystore/bycat) — preferred, no browser needed.
     Requires TABLEAU_PAT2_NAME + TABLEAU_PAT2_SECRET in .env.
  2. Selenium (RMDashboard-GPReport) — fallback if PAT2 not configured.
     Includes GP data but requires a browser + access key.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

from src.config import TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET
from src.db import init_db, upsert_category_rows
from src.tableau_downloader import (
    backfill_category_gmv_rest,
    backfill_category_performance,
)
from src.category_parser import parse_category_file

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

    use_rest = bool(TABLEAU_PAT2_NAME and TABLEAU_PAT2_SECRET)
    if use_rest:
        log.info("Using REST API (PAT2) — MonthlySalesbystore/bycat")
        _run_rest(dates)
    else:
        log.info("PAT2 not configured — using Selenium fallback (RMDashboard-GPReport)")
        _run_selenium(dates)


def _run_rest(dates):
    results = backfill_category_gmv_rest(dates)
    ok = 0
    print("\n===== Backfill summary (REST/GMV) =====")
    for d in dates:
        iso = d.strftime("%Y-%m-%d")
        rows = results.get(iso)
        if not rows:
            print(f"  {iso}: FAILED (no data)")
            continue
        try:
            upsert_category_rows(iso, rows)
            total_gmv = sum(r["gmv"] or 0 for r in rows)
            print(f"  {iso}: {len(rows)} rows, total GMV HKD {total_gmv:,.0f}")
            ok += 1
        except Exception as exc:
            print(f"  {iso}: store error — {exc}")
    print(f"===== {ok}/{len(dates)} days stored =====")


def _run_selenium(dates):
    results = backfill_category_performance(dates)
    ok = 0
    print("\n===== Backfill summary (Selenium/GMV+GP) =====")
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
