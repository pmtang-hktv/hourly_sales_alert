"""
Hourly sales performance alert for HKTVmall.

Schedule:
  :10 past every hour  — fetch latest email, store data, check for anomalies
  12:10                — also send half-day summary (00:00–12:00)
  00:10                — also send full-day summary for previous day
  15:00                — parse latest category performance xlsx, send summary
  08:15                — download Daily Sales Update PDF from Tableau, parse, send summary
"""
from __future__ import annotations

import fcntl
import logging
import os
import sys
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.analyzer import build_fullday_summary, build_halfday_summary, check_anomalies
from src.bot_listener import start_bot_listener
from src.category_analyzer import build_category_summary
from src.category_parser import parse_category_file
from src.config import CATEGORY_DIR, DB_PATH, TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET
from src.tableau_downloader import download_category_gmv_rest, download_category_performance, download_daily_sales_update, _parse_category_gmv_xlsx
from src.daily_parser import parse_daily_dashboard
from src.db import alert_sent, get_daily_dashboard, get_day_totals_upto_hour, get_hour_row, init_db, log_alert, upsert_category_rows, upsert_daily, upsert_daily_dashboard, upsert_hourly
from src.fetcher import fetch_latest_email
from src.notifier import send_telegram
from src.parser import parse_email
from src.reporter import format_anomaly_alert, format_category_summary, format_daily_dashboard_summary, format_fullday_summary, format_halfday_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def run_hourly_job():
    log.info("Hourly job started")

    raw = fetch_latest_email()
    if not raw:
        log.warning("No email found — skipping")
        return

    result = parse_email(raw["subject"], raw["body"])
    if not result:
        log.warning("Could not parse email: %s", raw.get("subject"))
        return

    report_date = result["report_date"]
    email_hour = result["email_hour"]
    log.info("Parsed email: report_date=%s email_hour=%02d rows=%d", report_date, email_hour, len(result["rows"]))

    for row in result["rows"]:
        upsert_hourly(row)

    # The 00:00 email covers the full previous day — store the daily summary
    if email_hour == 0 and result["summary"]:
        upsert_daily({"report_date": report_date, **result["summary"]})

    # Anomaly check on the most recently completed hour
    if result["rows"]:
        latest = result["rows"][-1]
        anomalies = check_anomalies(latest)
        if anomalies:
            key = f"anomaly_{report_date}_{latest['hour_slot']}"
            if not alert_sent(key):
                lw_date = (datetime.strptime(report_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
                last_week_row = get_hour_row(lw_date, latest["hour_slot"])
                day_totals = get_day_totals_upto_hour(report_date, latest["hour_start"])
                msg = format_anomaly_alert(anomalies, latest, last_week_row, day_totals)
                if send_telegram(msg):
                    log_alert(key, msg)
                    log.info("Sent anomaly alert for %s %s (%d metrics)", report_date, latest["hour_slot"], len(anomalies))

    # Scheduled summaries
    if email_hour == 12:
        _send_halfday_summary(report_date)
    elif email_hour == 0:
        _send_fullday_summary(report_date, result["summary"])


def _send_halfday_summary(report_date: str):
    key = f"halfday_{report_date}"
    if alert_sent(key):
        return
    data = build_halfday_summary(report_date)
    msg = format_halfday_summary(data, report_date)
    if send_telegram(msg):
        log_alert(key, msg)
        log.info("Sent half-day summary for %s", report_date)


def _send_fullday_summary(report_date: str, summary: dict):
    key = f"fullday_{report_date}"
    if alert_sent(key):
        return
    data = build_fullday_summary(report_date, summary)
    msg = format_fullday_summary(data)
    if send_telegram(msg):
        log_alert(key, msg)
        log.info("Sent full-day summary for %s", report_date)


def run_daily_dashboard_job():
    log.info("Daily dashboard job started")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    key = f"daily_dashboard_{yesterday}"
    if alert_sent(key):
        log.info("Daily dashboard already sent for %s", yesterday)
        return

    path = download_daily_sales_update()
    if not path:
        log.warning("Failed to download Daily Sales Update PDF from Tableau")
        return

    with open(path, "rb") as f:
        pdf_bytes = f.read()

    parsed = parse_daily_dashboard(pdf_bytes)
    if not parsed:
        log.warning("Failed to parse Daily Sales Update PDF")
        return

    import json
    upsert_daily_dashboard(yesterday, parsed, json.dumps(parsed))
    msg = format_daily_dashboard_summary(get_daily_dashboard(yesterday), yesterday)
    if send_telegram(msg):
        log_alert(key, msg)
        log.info("Sent daily dashboard summary for %s", yesterday)


def _latest_category_file() -> str | None:
    """Return the most recently modified .xlsx in CATEGORY_DIR, if any."""
    import glob
    import os
    os.makedirs(CATEGORY_DIR, exist_ok=True)
    files = glob.glob(os.path.join(CATEGORY_DIR, "*.xlsx"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _fetch_category_rows(yesterday: str) -> list | None:
    """Get category rows for `yesterday`. Prefers the REST API (PAT2) — fast, no
    browser. Falls back to the Selenium download + xlsx file parse if PAT2 is
    not configured or the REST call returns nothing."""
    if TABLEAU_PAT2_NAME and TABLEAU_PAT2_SECRET:
        rows = download_category_gmv_rest()
        if rows and any(r.get("gmv") for r in rows):
            log.info("Category rows via REST API: %d", len(rows))
            return rows
        log.warning("REST category download returned no rows or all-zero GMV — falling back to local file/Selenium")

    downloaded = download_category_performance()
    if downloaded:
        log.info("Auto-downloaded category file: %s", downloaded)
    else:
        log.info("Auto-download skipped or failed — checking for manually placed file")

    path = _latest_category_file()
    if not path:
        log.warning("No category file found in %s", CATEGORY_DIR)
        return None
    try:
        # REST API saves crosstab xlsx; try that parser first (gives real GMV).
        # Fall back to the Selenium/GPReport parser if it returns all zeros.
        with open(path, "rb") as fh:
            content = fh.read()
        rows = _parse_category_gmv_xlsx(content)
        if rows and any(r.get("gmv") for r in rows):
            log.info("Parsed category file with REST parser: %d rows", len(rows))
            return rows
        rows = parse_category_file(path)
        if rows and any(r.get("gmv") for r in rows):
            log.info("Parsed category file with Selenium parser: %d rows", len(rows))
            return rows
        log.warning("Category file parsed but all GMV is zero: %s", path)
        return None
    except Exception as exc:
        log.error("Failed to parse category file %s: %s", path, exc)
        return None


def run_category_job():
    log.info("Category performance job started")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    key = f"category_{yesterday}"
    if alert_sent(key):
        log.info("Category summary already sent for %s", yesterday)
        return

    rows = _fetch_category_rows(yesterday)
    if not rows:
        log.warning("No category rows for %s — aborting", yesterday)
        return

    upsert_category_rows(yesterday, rows)
    data = build_category_summary(yesterday)
    msg = format_category_summary(data)
    if send_telegram(msg):
        log_alert(key, msg)
        log.info("Sent category summary for %s (%d rows)", yesterday, len(rows))


# Held for the process lifetime so the OS keeps the exclusive lock — do not let it GC.
_LOCK_HANDLE = None


def _kill_stale_siblings():
    """Kill any other processes running this same script before we start.

    Handles the case where manually-launched `nohup python3 main.py` instances
    were started before the singleton lock existed and are still lingering alongside
    the launchd-managed instance. The lock alone cannot stop processes that predate
    it — we must terminate them explicitly at startup.
    """
    import signal
    import subprocess
    current_pid = os.getpid()
    script_marker = "hourly_sales_alert/main.py"
    try:
        result = subprocess.run(["pgrep", "-f", script_marker], capture_output=True, text=True)
        stale = [int(p) for p in result.stdout.split() if int(p) != current_pid]
        for pid in stale:
            log.warning("Terminating stale sibling instance (pid=%d)", pid)
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if stale:
            import time
            time.sleep(1)
    except Exception as exc:
        log.warning("Could not scan for stale siblings: %s", exc)


def _acquire_singleton_lock():
    """Ensure only one instance runs. Prevents duplicate Telegram pollers (which
    cause the same question to be answered multiple times) and duplicate scheduled
    sends. Exits if another instance already holds the lock."""
    global _LOCK_HANDLE
    lock_path = os.path.join(os.path.dirname(DB_PATH) or ".", ".sales_alert.lock")
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    _LOCK_HANDLE = open(lock_path, "w")
    try:
        fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("Another instance is already running (lock held: %s) — exiting", lock_path)
        sys.exit(1)
    _LOCK_HANDLE.write(str(os.getpid()))
    _LOCK_HANDLE.flush()
    log.info("Acquired single-instance lock (pid=%d): %s", os.getpid(), lock_path)


if __name__ == "__main__":
    _kill_stale_siblings()
    _acquire_singleton_lock()
    init_db()
    start_bot_listener()
    scheduler = BlockingScheduler(timezone="Asia/Hong_Kong")
    scheduler.add_job(run_hourly_job, CronTrigger(minute=10), id="hourly_job")
    scheduler.add_job(run_category_job, CronTrigger(hour=15, minute=0), id="category_job")
    scheduler.add_job(run_daily_dashboard_job, CronTrigger(hour=8, minute=15), id="daily_dashboard_job")
    log.info("Scheduler started — running at :10 past every hour (HKT)")
    log.info("Daily dashboard job scheduled at 08:15 HKT (Tableau PDF)")
    log.info("Category performance job scheduled at 15:00 HKT")
    log.info("Telegram bot listener running — send any question to your bot")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")
