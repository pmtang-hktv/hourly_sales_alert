from __future__ import annotations

from datetime import datetime, timedelta

from src.db import get_category_low_margin, get_category_main_totals

_MIN_GMV_FOR_ANOMALY = 50_000   # ignore tiny categories
_ANOMALY_PCT_THRESHOLD = 25.0   # flag if |pct diff| >= 25% vs same-weekday avg


def detect_category_anomalies(report_date: str, today: list[dict]) -> list[dict]:
    """Compare each main-category's GMV against the same weekday over the past 4 weeks.
    Returns up to 6 anomalies (top 3 high + top 3 low) sorted by absolute % deviation."""
    date_obj = datetime.strptime(report_date, "%Y-%m-%d")
    history_by_cat: dict[str, list[float]] = {}
    for weeks_back in range(1, 5):
        prior_date = (date_obj - timedelta(days=7 * weeks_back)).strftime("%Y-%m-%d")
        for row in get_category_main_totals(prior_date):
            history_by_cat.setdefault(row["main_cat"], []).append(float(row["gmv"] or 0))

    today_by_cat = {c["main_cat"]: float(c["gmv"] or 0) for c in today}
    anomalies = []
    for cat, gmv in today_by_cat.items():
        history = history_by_cat.get(cat, [])
        if len(history) < 2:
            continue
        mean = sum(history) / len(history)
        if mean < _MIN_GMV_FOR_ANOMALY:
            continue
        pct_diff = (gmv - mean) / mean * 100
        if abs(pct_diff) < _ANOMALY_PCT_THRESHOLD:
            continue
        anomalies.append({
            "main_cat": cat,
            "gmv": gmv,
            "mean": mean,
            "pct_diff": round(pct_diff, 1),
            "direction": "high" if pct_diff > 0 else "low",
            "weeks_compared": len(history),
        })

    anomalies.sort(key=lambda x: abs(x["pct_diff"]), reverse=True)
    gainers = [a for a in anomalies if a["direction"] == "high"][:3]
    losers  = [a for a in anomalies if a["direction"] == "low"][:3]
    return gainers + losers


def build_category_summary(report_date: str) -> dict:
    """Assemble the daily category performance summary for Telegram."""
    today = get_category_main_totals(report_date)
    total_gmv = sum(c["gmv"] for c in today)
    total_gp = sum(c["gp"] for c in today)
    # GP is only present when sourced from the Selenium flow; the REST GMV view has none.
    has_gp = any(c["gp"] for c in today)

    # Week-over-week comparison at main-category level
    lw_date = (datetime.strptime(report_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    last_week = {c["main_cat"]: c["gmv"] for c in get_category_main_totals(lw_date)}

    movers = []
    if last_week:
        for c in today:
            lw_gmv = last_week.get(c["main_cat"])
            if lw_gmv and lw_gmv > 0:
                diff_pct = (c["gmv"] - lw_gmv) / lw_gmv * 100
                movers.append({
                    "main_cat": c["main_cat"],
                    "gmv": c["gmv"],
                    "lw_gmv": lw_gmv,
                    "diff_pct": diff_pct,
                    "diff_abs": c["gmv"] - lw_gmv,
                })
        # Largest absolute swings (up or down)
        movers.sort(key=lambda m: abs(m["diff_abs"]), reverse=True)

    # Skip the low-margin query when GP is absent — otherwise gp_pct<=0 matches everything.
    low_margin = get_category_low_margin(report_date, min_gmv=5000, max_gp_pct=0.0) if has_gp else []

    anomalies = detect_category_anomalies(report_date, today)

    return {
        "report_date": report_date,
        "total_gmv": total_gmv,
        "total_gp": total_gp,
        "total_gp_pct": (total_gp / total_gmv) if total_gmv else 0,
        "has_gp": has_gp,
        "top_categories": today[:8],
        "has_last_week": bool(last_week),
        "movers": movers[:6],
        "low_margin": low_margin[:8],
        "anomalies": anomalies,
    }
