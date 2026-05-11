"""
Hourly sales performance alert for HKTVmall.

Schedule:
  :10 past every hour  — fetch latest email, store data, check for anomalies
  12:10                — also send half-day summary (00:00–12:00)
  00:10                — also send full-day summary for previous day
"""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.analyzer import build_fullday_summary, build_halfday_summary, check_anomalies
from src.bot_listener import start_bot_listener
from src.db import alert_sent, get_day_totals_upto_hour, get_hour_row, init_db, log_alert, upsert_daily, upsert_hourly
from src.fetcher import fetch_latest_email
from src.notifier import send_telegram
from src.parser import parse_email
from src.reporter import format_anomaly_alert, format_fullday_summary, format_halfday_summary

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


if __name__ == "__main__":
    init_db()
    start_bot_listener()
    scheduler = BlockingScheduler(timezone="Asia/Hong_Kong")
    scheduler.add_job(run_hourly_job, CronTrigger(minute=10), id="hourly_job")
    log.info("Scheduler started — running at :10 past every hour (HKT)")
    log.info("Telegram bot listener running — send any question to your bot")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")
