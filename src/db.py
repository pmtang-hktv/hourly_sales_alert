from __future__ import annotations

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

CREATE TABLE IF NOT EXISTS daily_dashboard (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date              TEXT NOT NULL UNIQUE,
    gmv                      REAL,
    mtd_gmv                  REAL,
    gmv_projection           REAL,
    gmv_per_order            REAL,
    net_sales                REAL,
    mtd_net_sales            REAL,
    num_orders               INTEGER,
    num_customers            INTEGER,
    conversion_rate          REAL,
    mainland_yesterday_gmv   REAL,
    mainland_yesterday_orders INTEGER,
    mainland_yesterday_customers INTEGER,
    mainland_yesterday_gmv_per_order REAL,
    mainland_mtd_gmv         REAL,
    mainland_mtd_orders      INTEGER,
    igloo_yesterday_gmv      REAL,
    igloo_yesterday_orders   INTEGER,
    igloo_mtd_gmv            REAL,
    igloo_mtd_orders         INTEGER,
    theplace_yesterday_gmv   REAL,
    theplace_yesterday_orders INTEGER,
    theplace_yesterday_customers INTEGER,
    theplace_yesterday_gmv_per_order REAL,
    theplace_mtd_gmv         REAL,
    theplace_mtd_orders      INTEGER,
    insurance_yesterday_gmv  REAL,
    insurance_yesterday_orders INTEGER,
    insurance_mtd_gmv        REAL,
    insurance_mtd_orders     INTEGER,
    normal_yesterday_gmv     REAL,
    normal_yesterday_pct     REAL,
    threpl_yesterday_gmv     REAL,
    threpl_yesterday_pct     REAL,
    normal_mtd_gmv           REAL,
    normal_mtd_pct           REAL,
    threpl_mtd_gmv           REAL,
    threpl_mtd_pct           REAL,
    sameday_yesterday_pct    REAL,
    sameday_mtd_pct          REAL,
    raw_json                 TEXT,
    created_at               TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS category_performance (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date  TEXT NOT NULL,
    main_cat     TEXT,
    sub_cat1     TEXT,
    sub_cat2     TEXT,
    sub_cat3     TEXT,
    sub_cat4     TEXT,
    leaf_cat     TEXT,
    level        INTEGER,
    gmv          REAL,
    gp           REAL,
    gp_pct       REAL,
    created_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(report_date, main_cat, sub_cat1, sub_cat2, sub_cat3, sub_cat4)
);

CREATE INDEX IF NOT EXISTS idx_cat_date ON category_performance(report_date);
CREATE INDEX IF NOT EXISTS idx_cat_main ON category_performance(main_cat);

CREATE TABLE IF NOT EXISTS store_performance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    team_head   TEXT,
    rm_code     TEXT,
    rm_name     TEXT,
    store_code  TEXT NOT NULL,
    store_name  TEXT,
    main_cat    TEXT,
    gmv         REAL,
    customers   INTEGER,
    orders      INTEGER,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(report_date, store_code, main_cat)
);

CREATE INDEX IF NOT EXISTS idx_store_date ON store_performance(report_date);
CREATE INDEX IF NOT EXISTS idx_store_rm   ON store_performance(rm_code);

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
    # Migrations: add columns/indexes missing from older DB instances
    with get_conn() as conn:
        for sql in [
            "ALTER TABLE store_performance ADD COLUMN team_head TEXT",
            "CREATE INDEX IF NOT EXISTS idx_store_teamhead ON store_performance(team_head)",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass  # column/index already exists


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


def get_day_totals_upto_hour(report_date: str, hour_start: int) -> dict:
    sql = """
        SELECT SUM(total_order) as orders,
               SUM(total_order * basket_size) as gmv,
               MAX(hour_start) as last_hour_start
        FROM hourly_stats
        WHERE report_date = ? AND hour_start <= ?
    """
    with get_conn() as conn:
        row = conn.execute(sql, (report_date, hour_start)).fetchone()
    return dict(row) if row else {"orders": 0, "gmv": 0, "last_hour_start": hour_start}


def get_hour_row(report_date: str, hour_slot: str) -> dict | None:
    sql = "SELECT * FROM hourly_stats WHERE report_date = ? AND hour_slot = ?"
    with get_conn() as conn:
        row = conn.execute(sql, (report_date, hour_slot)).fetchone()
    return dict(row) if row else None


def get_hours_for_date(report_date: str) -> list[dict]:
    sql = "SELECT * FROM hourly_stats WHERE report_date = ? ORDER BY hour_start"
    with get_conn() as conn:
        rows = conn.execute(sql, (report_date,)).fetchall()
    return [dict(r) for r in rows]


def upsert_daily_dashboard(report_date: str, parsed: dict, raw_json: str):
    import json as _json
    tl = parsed.get("top_level") or {}
    mm = parsed.get("mainland_merchant") or {}
    ig = parsed.get("igloo_plus") or {}
    tp = parsed.get("theplace") or {}
    ins = parsed.get("insurance") or {}
    onl = parsed.get("online_normal_vs_3pl") or {}
    sd = parsed.get("same_day_delivery") or {}

    row = {
        "report_date": report_date,
        "gmv": tl.get("gmv"), "mtd_gmv": tl.get("mtd_gmv"),
        "gmv_projection": tl.get("gmv_projection"), "gmv_per_order": tl.get("gmv_per_order"),
        "net_sales": tl.get("net_sales"), "mtd_net_sales": tl.get("mtd_net_sales"),
        "num_orders": tl.get("num_orders"), "num_customers": tl.get("num_customers"),
        "conversion_rate": tl.get("conversion_rate"),
        "mainland_yesterday_gmv": mm.get("yesterday_gmv"),
        "mainland_yesterday_orders": mm.get("yesterday_orders"),
        "mainland_yesterday_customers": mm.get("yesterday_customers"),
        "mainland_yesterday_gmv_per_order": mm.get("yesterday_gmv_per_order"),
        "mainland_mtd_gmv": mm.get("mtd_gmv"), "mainland_mtd_orders": mm.get("mtd_orders"),
        "igloo_yesterday_gmv": ig.get("yesterday_gmv"),
        "igloo_yesterday_orders": ig.get("yesterday_orders"),
        "igloo_mtd_gmv": ig.get("mtd_gmv"), "igloo_mtd_orders": ig.get("mtd_orders"),
        "theplace_yesterday_gmv": tp.get("yesterday_gmv"),
        "theplace_yesterday_orders": tp.get("yesterday_orders"),
        "theplace_yesterday_customers": tp.get("yesterday_customers"),
        "theplace_yesterday_gmv_per_order": tp.get("yesterday_gmv_per_order"),
        "theplace_mtd_gmv": tp.get("mtd_gmv"), "theplace_mtd_orders": tp.get("mtd_orders"),
        "insurance_yesterday_gmv": ins.get("yesterday_gmv"),
        "insurance_yesterday_orders": ins.get("yesterday_orders"),
        "insurance_mtd_gmv": ins.get("mtd_gmv"), "insurance_mtd_orders": ins.get("mtd_orders"),
        "normal_yesterday_gmv": onl.get("yesterday_normal_gmv"),
        "normal_yesterday_pct": onl.get("yesterday_normal_pct"),
        "threpl_yesterday_gmv": onl.get("yesterday_3pl_gmv"),
        "threpl_yesterday_pct": onl.get("yesterday_3pl_pct"),
        "normal_mtd_gmv": onl.get("mtd_normal_gmv"), "normal_mtd_pct": onl.get("mtd_normal_pct"),
        "threpl_mtd_gmv": onl.get("mtd_3pl_gmv"), "threpl_mtd_pct": onl.get("mtd_3pl_pct"),
        "sameday_yesterday_pct": sd.get("yesterday_gmv_pct"),
        "sameday_mtd_pct": sd.get("mtd_gmv_pct"),
        "raw_json": raw_json,
    }
    cols = list(row.keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    updates = ", ".join([f"{c}=excluded.{c}" for c in cols if c != "report_date"])
    sql = f"""
        INSERT INTO daily_dashboard ({col_names}) VALUES ({placeholders})
        ON CONFLICT(report_date) DO UPDATE SET {updates}
    """
    with get_conn() as conn:
        conn.execute(sql, list(row.values()))


def get_daily_dashboard(report_date: str) -> dict | None:
    sql = "SELECT * FROM daily_dashboard WHERE report_date = ?"
    with get_conn() as conn:
        row = conn.execute(sql, (report_date,)).fetchone()
    return dict(row) if row else None


def upsert_category_rows(report_date: str, rows: list[dict]):
    """Replace all category rows for a date, then bulk-insert the new set."""
    with get_conn() as conn:
        conn.execute("DELETE FROM category_performance WHERE report_date = ?", (report_date,))
        conn.executemany(
            """INSERT INTO category_performance
               (report_date, main_cat, sub_cat1, sub_cat2, sub_cat3, sub_cat4,
                leaf_cat, level, gmv, gp, gp_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (report_date, r["main_cat"], r["sub_cat1"], r["sub_cat2"],
                 r["sub_cat3"], r["sub_cat4"], r["leaf_cat"], r["level"],
                 r["gmv"], r["gp"], r["gp_pct"])
                for r in rows
            ],
        )


def get_category_main_totals(report_date: str) -> list[dict]:
    """GMV / GP aggregated to main category for a given date."""
    sql = """
        SELECT main_cat,
               SUM(gmv) AS gmv,
               SUM(gp)  AS gp,
               CASE WHEN SUM(gmv) > 0 THEN SUM(gp) / SUM(gmv) ELSE 0 END AS gp_pct
        FROM category_performance
        WHERE report_date = ?
        GROUP BY main_cat
        ORDER BY gmv DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (report_date,)).fetchall()]


def get_category_low_margin(report_date: str, min_gmv: float = 5000, max_gp_pct: float = 0.0) -> list[dict]:
    """Leaf categories with meaningful GMV but margin at or below a threshold."""
    sql = """
        SELECT leaf_cat, main_cat, gmv, gp, gp_pct
        FROM category_performance
        WHERE report_date = ? AND gmv >= ? AND gp_pct <= ?
        ORDER BY gmv DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (report_date, min_gmv, max_gp_pct)).fetchall()]


def get_category_dates() -> list[str]:
    sql = "SELECT DISTINCT report_date FROM category_performance ORDER BY report_date DESC"
    with get_conn() as conn:
        return [r[0] for r in conn.execute(sql).fetchall()]


def upsert_store_rows(report_date: str, rows: list[dict]):
    """Replace all store rows for a date, then bulk-insert the new set."""
    with get_conn() as conn:
        conn.execute("DELETE FROM store_performance WHERE report_date = ?", (report_date,))
        conn.executemany(
            """INSERT INTO store_performance
               (report_date, team_head, rm_code, rm_name, store_code, store_name, main_cat,
                gmv, customers, orders)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (report_date, r.get("team_head"), r["rm_code"], r["rm_name"], r["store_code"],
                 r["store_name"], r["main_cat"], r["gmv"], r["customers"], r["orders"])
                for r in rows
            ],
        )


def get_store_totals(report_date: str) -> list[dict]:
    """Total GMV/customers/orders per store for a given date, ordered by GMV DESC."""
    sql = """
        SELECT store_code, store_name, team_head, rm_code, rm_name,
               SUM(gmv)       AS gmv,
               SUM(customers) AS customers,
               SUM(orders)    AS orders
        FROM store_performance
        WHERE report_date = ?
        GROUP BY store_code
        ORDER BY gmv DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (report_date,)).fetchall()]


def get_store_dates() -> list[str]:
    sql = "SELECT DISTINCT report_date FROM store_performance ORDER BY report_date DESC"
    with get_conn() as conn:
        return [r[0] for r in conn.execute(sql).fetchall()]


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
