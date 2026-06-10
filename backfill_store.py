"""Backfill historical Store Performance (GMV by store × main category) from Tableau.

Usage:
  python3 backfill_store.py [DAYS]

  DAYS defaults to 180. Downloads the past DAYS days (ending yesterday) from
  MonthlySalesbystore/bystorebymaincat via REST API (PAT2), stores rows in the DB,
  and prints a summary. Does NOT send Telegram messages.

Requires TABLEAU_PAT2_NAME + TABLEAU_PAT2_SECRET in .env.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

from src.config import TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET
from src.db import init_db, upsert_store_rows
from src.tableau_downloader import backfill_store_gmv_rest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_store")


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    yesterday = (datetime.now() - timedelta(days=1)).date()
    dates = [yesterday - timedelta(days=i) for i in range(days)]  # newest first
    log.info("Backfilling %d days: %s … %s", days, dates[-1], dates[0])

    if not (TABLEAU_PAT2_NAME and TABLEAU_PAT2_SECRET):
        log.error("TABLEAU_PAT2_NAME / TABLEAU_PAT2_SECRET not set in .env — cannot proceed")
        sys.exit(1)

    init_db()

    results = backfill_store_gmv_rest(dates)
    ok = 0
    print("\n===== Backfill summary (Store GMV via REST) =====")
    for d in dates:
        iso = d.strftime("%Y-%m-%d")
        rows = results.get(iso)
        if not rows:
            print(f"  {iso}: FAILED (no data)")
            continue
        try:
            upsert_store_rows(iso, rows)
            total_gmv = sum(r["gmv"] or 0 for r in rows)
            print(f"  {iso}: {len(rows)} rows, total GMV HKD {total_gmv:,.0f}")
            ok += 1
        except Exception as exc:
            print(f"  {iso}: store error — {exc}")
    print(f"===== {ok}/{len(dates)} days stored =====")


if __name__ == "__main__":
    main()
