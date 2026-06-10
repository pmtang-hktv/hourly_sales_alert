from __future__ import annotations

from datetime import datetime, timedelta

from src.db import get_category_low_margin, get_category_main_totals


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
    }
