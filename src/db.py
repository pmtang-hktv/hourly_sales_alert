import os
import sqlite3
from contextlib import contextmanager
from src.config import DB_PATH

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS hourly_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date     TEXT NOT NULL,
    hour_slot       TEXT NOT NULL,
    hour_start      INTEGER NOT NULL,
    total_order     INTEGER,
    total_payment   INTEGER,
    basket_size     REAL,
    asiapay         INTEGER DEFAULT 0,
    globalpay       INTEGER DEFAULT 0,
    mpgs            INTEGER DEFAULT 0,
    amex            INTEGER DEFAULT 0,
    paypal          INTEGER DEFAULT 0,
    payme           INTEGER DEFAULT 0,
    octopus         INTEGER DEFAULT 0,
    atome           INTEGER DEFAULT 0,
    unionpay        INTEGER DEFAULT 0,
    alipayhk        INTEGER DEFAULT 0,
    wechatpay       INTEGER DEFAULT 0,
    wechatmp        INTEGER DEFAULT 0,
    bocpay          INTEGER DEFAULT 0,
    citicobrand     INTEGER DEFAULT 0,
    mpay            INTEGER DEFAULT 0,
    applepay        INTEGER DEFAULT 0,
    mfood           INTEGER DEFAULT 0,
    ctm             INTEGER DEFAULT 0,
    fps             INTEGER DEFAULT 0,
    pos_offline     INTEGER DEFAULT 0,
    pos_online      INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(report_date, hour_slot)
);

CREATE TABLE IF NOT EXISTS daily_summary (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date           TEXT NOT NULL UNIQUE,
    total_order_success   INTEGER,
    total_order_over_2000 INTEGER,
    total_amount          REAL,
    avg_amount_per_order  REAL,
    created_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key  TEXT NOT NULL UNIQUE,
    message    TEXT,
    sent_at    TEXT DEFAULT (datetime('now'))
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_hourly(row: dict):
    cols = list(row.keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    updates = ", ".join([f"{c}=excluded.{c}" for c in cols if c not in ("report_date", "hour_slot")])
    sql = f"""
        INSERT INTO hourly_stats ({col_names}) VALUES ({placeholders})
        ON CONFLICT(report_date, hour_slot) DO UPDATE SET {updates}
    """
    with get_conn() as conn:
        conn.execute(sql, list(row.values()))


def upsert_daily(row: dict):
    cols = list(row.keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    updates = ", ".join([f"{c}=excluded.{c}" for c in cols if c != "report_date"])
    sql = f"""
        INSERT INTO daily_summary ({col_names}) VALUES ({placeholders})
        ON CONFLICT(report_date) DO UPDATE SET {updates}
    """
    with get_conn() as conn:
        conn.execute(sql, list(row.values()))


def get_hourly_history(hour_start: int, weeks: int = 4) -> list[dict]:
    sql = """
        SELECT * FROM hourly_stats
        WHERE hour_start = ?
          AND report_date >= date('now', ?)
        ORDER BY report_date DESC
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (hour_start, f"-{weeks * 7} days")).fetchall()
    return [dict(r) for r in rows]


def get_daily_history(weeks: int = 5) -> list[dict]:
    sql = """
        SELECT * FROM daily_summary
        WHERE report_date >= date('now', ?)
        ORDER BY report_date DESC
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (f"-{weeks * 7} days",)).fetchall()
    return [dict(r) for r in rows]


def get_hours_for_date(report_date: str) -> list[dict]:
    sql = "SELECT * FROM hourly_stats WHERE report_date = ? ORDER BY hour_start"
    with get_conn() as conn:
        rows = conn.execute(sql, (report_date,)).fetchall()
    return [dict(r) for r in rows]


def alert_sent(key: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM alerts_log WHERE alert_key = ?", (key,)).fetchone()
    return row is not None


def log_alert(key: str, message: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO alerts_log (alert_key, message) VALUES (?, ?)",
            (key, message),
        )
