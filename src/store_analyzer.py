from __future__ import annotations

from datetime import datetime, timedelta

from src.db import get_store_totals

_MIN_GMV_FOR_ANOMALY = 10_000   # ignore tiny stores
_ANOMALY_PCT_THRESHOLD = 25.0   # flag if |pct diff| >= 25% vs same-weekday avg


def detect_store_anomalies(report_date: str, today: list[dict]) -> list[dict]:
    """Compare each store's GMV against the same weekday over the past 4 weeks.
    Returns up to 5 gainers + 5 losers sorted by absolute % deviation."""
    date_obj = datetime.strptime(report_date, "%Y-%m-%d")
    history_by_store: dict[str, list[float]] = {}
    for weeks_back in range(1, 5):
        prior_date = (date_obj - timedelta(days=7 * weeks_back)).strftime("%Y-%m-%d")
        for row in get_store_totals(prior_date):
            history_by_store.setdefault(row["store_code"], []).append(float(row["gmv"] or 0))

    today_by_code = {s["store_code"]: s for s in today}
    anomalies = []
    for code, store in today_by_code.items():
        gmv = float(store["gmv"] or 0)
        history = history_by_store.get(code, [])
        if len(history) < 2:
            continue
        mean = sum(history) / len(history)
        if mean < _MIN_GMV_FOR_ANOMALY:
            continue
        pct_diff = (gmv - mean) / mean * 100
        if abs(pct_diff) < _ANOMALY_PCT_THRESHOLD:
            continue
        anomalies.append({
            "store_code":     code,
            "store_name":     store["store_name"],
            "rm_name":        store["rm_name"],
            "gmv":            gmv,
            "mean":           mean,
            "pct_diff":       round(pct_diff, 1),
            "direction":      "high" if pct_diff > 0 else "low",
            "weeks_compared": len(history),
        })

    anomalies.sort(key=lambda x: abs(x["pct_diff"]), reverse=True)
    gainers = [a for a in anomalies if a["direction"] == "high"][:5]
    losers  = [a for a in anomalies if a["direction"] == "low"][:5]
    return gainers + losers


def build_store_summary(report_date: str) -> dict:
    """Assemble the daily store performance summary for Telegram."""
    date_obj = datetime.strptime(report_date, "%Y-%m-%d")
    today = get_store_totals(report_date)
    total_gmv    = sum(s["gmv"] or 0 for s in today)
    total_orders = sum(s["orders"] or 0 for s in today)
    total_cust   = sum(s["customers"] or 0 for s in today)

    lw_date = (date_obj - timedelta(days=7)).strftime("%Y-%m-%d")
    lw_stores = get_store_totals(lw_date)
    lw_total_gmv = sum(s["gmv"] or 0 for s in lw_stores)

    anomalies = detect_store_anomalies(report_date, today)

    return {
        "report_date":   report_date,
        "weekday_name":  date_obj.strftime("%A"),
        "total_gmv":     total_gmv,
        "total_stores":  len(today),
        "total_orders":  total_orders,
        "total_customers": total_cust,
        "top_stores":    today[:15],
        "lw_total_gmv":  lw_total_gmv,
        "has_last_week": bool(lw_stores),
        "anomalies":     anomalies,
    }
