from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from src.config import ANOMALY_Z_THRESHOLD, MIN_GATEWAY_VOLUME
from src.db import get_daily_history, get_hourly_history, get_hours_for_date
from src.parser import GATEWAY_COLS


def check_anomalies(current_row: dict) -> list[dict]:
    """
    Compare current_row against the same hour slot across the past 4 weeks.
    Returns a list of anomaly dicts for any metric exceeding ANOMALY_Z_THRESHOLD.
    """
    hour_start = current_row["hour_start"]
    report_date = current_row["report_date"]
    history = [h for h in get_hourly_history(hour_start, weeks=4) if h["report_date"] != report_date]

    if len(history) < 3:
        return []

    anomalies: list[dict] = []

    _check_metric(current_row, history, "total_order", "Total Orders", anomalies)
    _check_metric(current_row, history, "basket_size", "Basket Size ($)", anomalies)

    # GMV = total_order * basket_size (not stored directly, computed per row)
    gmv_current = (current_row.get("total_order") or 0) * (current_row.get("basket_size") or 0)
    gmv_history = [
        {**h, "_gmv": (h.get("total_order") or 0) * (h.get("basket_size") or 0)}
        for h in history
    ]
    _check_metric({"_gmv": gmv_current}, gmv_history, "_gmv", "GMV (HKD)", anomalies)

    for gw in GATEWAY_COLS:
        avg = np.mean([h.get(gw, 0) or 0 for h in history])
        if avg < MIN_GATEWAY_VOLUME:
            continue
        _check_metric(current_row, history, gw, gw.replace("_", " ").title(), anomalies)

    return anomalies


def _check_metric(row: dict, history: list[dict], col: str, label: str, out: list):
    values = [h.get(col) or 0 for h in history]
    if len(values) < 3:
        return
    mean = float(np.mean(values))
    std = float(np.std(values))
    if std < 1:
        return
    current = float(row.get(col) or 0)
    z = (current - mean) / std
    if abs(z) >= ANOMALY_Z_THRESHOLD:
        pct = round((current - mean) / mean * 100, 1) if mean else 0
        out.append({
            "metric":    col,
            "label":     label,
            "current":   current,
            "mean":      round(mean, 1),
            "std":       round(std, 1),
            "z_score":   round(z, 2),
            "direction": "high" if z > 0 else "low",
            "pct_diff":  pct,
        })


def _row_gmv(rows: list[dict]) -> float:
    return sum((r.get("total_order") or 0) * (r.get("basket_size") or 0) for r in rows)


def build_halfday_summary(report_date: str) -> dict:
    today_rows = [r for r in get_hours_for_date(report_date) if r["hour_start"] < 12]
    date_obj = datetime.strptime(report_date, "%Y-%m-%d")

    last_week_date = (date_obj - timedelta(days=7)).strftime("%Y-%m-%d")
    last_week_rows = [r for r in get_hours_for_date(last_week_date) if r["hour_start"] < 12]

    four_week_orders, four_week_gmv = [], []
    for w in range(1, 5):
        d = (date_obj - timedelta(days=7 * w)).strftime("%Y-%m-%d")
        rows = [r for r in get_hours_for_date(d) if r["hour_start"] < 12]
        if rows:
            four_week_orders.append(sum(r["total_order"] for r in rows))
            four_week_gmv.append(_row_gmv(rows))

    today_orders = sum(r["total_order"] for r in today_rows)
    today_gmv = _row_gmv(today_rows)
    lw_orders = sum(r["total_order"] for r in last_week_rows)
    lw_gmv = _row_gmv(last_week_rows)

    best_hour = max(today_rows, key=lambda r: r["total_order"]) if today_rows else None
    worst_hour = min(today_rows, key=lambda r: r["total_order"]) if today_rows else None

    return {
        "today_orders":     today_orders,
        "today_gmv":        today_gmv,
        "today_avg_basket": today_gmv / today_orders if today_orders else 0,
        "lw_orders":        lw_orders,
        "lw_gmv":           lw_gmv,
        "fw_avg_orders":    round(float(np.mean(four_week_orders)), 0) if four_week_orders else None,
        "fw_avg_gmv":       round(float(np.mean(four_week_gmv)), 0) if four_week_gmv else None,
        "best_hour":        best_hour,
        "worst_hour":       worst_hour,
        "weekday_name":     date_obj.strftime("%A"),
        "report_date":      report_date,
    }


def build_fullday_summary(report_date: str, daily_summary: dict) -> dict:
    date_obj = datetime.strptime(report_date, "%Y-%m-%d")
    last_week_date = (date_obj - timedelta(days=7)).strftime("%Y-%m-%d")

    history = get_daily_history(weeks=5)
    last_week = next((h for h in history if h["report_date"] == last_week_date), None)
    same_weekday = [
        h for h in history
        if h["report_date"] != report_date
        and datetime.strptime(h["report_date"], "%Y-%m-%d").weekday() == date_obj.weekday()
    ][:4]

    today_rows = get_hours_for_date(report_date)
    best_hour = max(today_rows, key=lambda r: r["total_order"]) if today_rows else None
    worst_hour = min(today_rows, key=lambda r: r["total_order"]) if today_rows else None

    # Gateway mix: share of total_payment per gateway
    gw_share_today = _gateway_shares(today_rows)
    last_week_rows = get_hours_for_date(last_week_date) if last_week_date else []
    gw_share_lw = _gateway_shares(last_week_rows)
    gw_shifts = _notable_gateway_shifts(gw_share_today, gw_share_lw)

    return {
        "report_date":          report_date,
        "weekday_name":         date_obj.strftime("%A"),
        "daily_summary":        daily_summary,
        "last_week":            last_week,
        "four_week_avg_orders": round(float(np.mean([h["total_order_success"] for h in same_weekday])), 0) if same_weekday else None,
        "four_week_avg_amount": round(float(np.mean([h["total_amount"] for h in same_weekday])), 0) if same_weekday else None,
        "best_hour":            best_hour,
        "worst_hour":           worst_hour,
        "gw_shifts":            gw_shifts,
    }


def _gateway_shares(rows: list[dict]) -> dict[str, float]:
    total = sum(r.get("total_payment", 0) or 0 for r in rows)
    if not total:
        return {}
    shares = {}
    for gw in GATEWAY_COLS:
        vol = sum(r.get(gw, 0) or 0 for r in rows)
        if vol > 0:
            shares[gw] = vol / total * 100
    return shares


def _notable_gateway_shifts(today: dict, last_week: dict, threshold: float = 3.0) -> list[dict]:
    shifts = []
    all_gws = set(today) | set(last_week)
    for gw in all_gws:
        t = today.get(gw, 0)
        lw = last_week.get(gw, 0)
        if lw < 1:
            continue
        diff = t - lw
        if abs(diff) >= threshold:
            shifts.append({"gw": gw.replace("_", " ").title(), "today_pct": round(t, 1), "lw_pct": round(lw, 1), "diff": round(diff, 1)})
    shifts.sort(key=lambda x: abs(x["diff"]), reverse=True)
    return shifts[:5]
