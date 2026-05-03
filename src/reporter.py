from __future__ import annotations


def format_anomaly_alert(anomalies: list[dict], row: dict) -> str:
    date = row["report_date"]
    slot = row["hour_slot"]
    total_orders = int(row.get("total_order") or 0)

    lines = [f"⚠️ *Sales Anomaly — {date} {slot}*", f"Total Orders this hour: *{total_orders:,}*", ""]

    for a in anomalies:
        icon = "📈" if a["direction"] == "high" else "📉"
        sign = "+" if a["pct_diff"] > 0 else ""
        current = _fmt(a["current"])
        avg = _fmt(a["mean"])
        lines.append(
            f"{icon} *{a['label']}*: {current} "
            f"({sign}{a['pct_diff']}% vs 4-wk avg {avg}, Z={a['z_score']})"
        )

    return "\n".join(lines)


def format_halfday_summary(data: dict, report_date: str) -> str:
    today = data["today_total"]
    lw = data["last_week_total"]
    fw_avg = data["four_week_avg"]
    weekday = data["weekday_name"]

    lines = [f"🌅 *Half-Day Summary — {weekday} {report_date}*", f"Period: 00:00 – 12:00", ""]
    lines.append(f"Orders: *{today:,}*")

    if lw:
        icon, diff = _compare(today, lw)
        lines.append(f"{icon} vs last {weekday}: {lw:,} ({diff})")

    if fw_avg:
        icon, diff = _compare(today, fw_avg)
        lines.append(f"{icon} vs 4-wk {weekday} avg: {fw_avg:,.0f} ({diff})")

    if data.get("today_rows"):
        top3 = sorted(data["today_rows"], key=lambda r: r["total_order"], reverse=True)[:3]
        top_str = ", ".join(f"{r['hour_slot']} ({r['total_order']:,})" for r in top3)
        lines.append(f"\nTop 3 hours: {top_str}")

    # Pace vs last week at same point in morning
    if lw and fw_avg:
        lw_pace = round(today / lw * 100, 1) if lw else None
        lines.append(f"\nMorning pace vs last week: *{lw_pace}%*")

    return "\n".join(lines)


def format_fullday_summary(data: dict) -> str:
    ds = data["daily_summary"]
    lw = data["last_week"]
    fw_orders = data["four_week_avg_orders"]
    fw_amount = data["four_week_avg_amount"]
    weekday = data["weekday_name"]
    date = data["report_date"]

    total_orders = int(ds.get("total_order_success") or 0)
    total_amount = float(ds.get("total_amount") or 0)
    avg_order = float(ds.get("avg_amount_per_order") or 0)
    over_2k = int(ds.get("total_order_over_2000") or 0)

    lines = [f"📊 *Daily Summary — {weekday} {date}*", ""]
    lines.append(f"Total Orders:      *{total_orders:,}*")
    lines.append(f"Total Revenue:     *${total_amount:,.0f}*")
    lines.append(f"Avg Order Value:   *${avg_order:.2f}*")
    lines.append(f"Orders >$2,000:    *{over_2k:,}* ({_pct(over_2k, total_orders)} of orders)")

    if lw:
        icon, diff = _compare(total_orders, lw["total_order_success"])
        icon2, diff2 = _compare(total_amount, lw["total_amount"])
        lines.append(f"\n{icon} vs last {weekday}: {lw['total_order_success']:,} orders ({diff}), ${lw['total_amount']:,.0f} ({diff2})")

    if fw_orders:
        icon, diff = _compare(total_orders, fw_orders)
        icon2, diff2 = _compare(total_amount, fw_amount)
        lines.append(f"{icon} vs 4-wk {weekday} avg: {fw_orders:,.0f} orders ({diff}), ${fw_amount:,.0f} ({diff2})")

    if data.get("best_hour"):
        b, w = data["best_hour"], data["worst_hour"]
        lines.append(f"\n🏆 Best hour:    {b['hour_slot']} — {b['total_order']:,} orders (${b['basket_size']:.0f} basket)")
        lines.append(f"🔻 Slowest hour: {w['hour_slot']} — {w['total_order']:,} orders (${w['basket_size']:.0f} basket)")

    if data.get("gw_shifts"):
        lines.append("\n💳 *Notable payment gateway shifts vs last week:*")
        for s in data["gw_shifts"]:
            icon = "📈" if s["diff"] > 0 else "📉"
            sign = "+" if s["diff"] > 0 else ""
            lines.append(f"  {icon} {s['gw']}: {s['today_pct']}% ({sign}{s['diff']}pp)")

    return "\n".join(lines)


def _fmt(v) -> str:
    if isinstance(v, float) and v != int(v):
        return f"{v:,.1f}"
    return f"{int(v):,}"


def _compare(current, base) -> tuple[str, str]:
    icon = "📈" if current >= base else "📉"
    if not base:
        return icon, "N/A"
    diff = (current - base) / base * 100
    sign = "+" if diff >= 0 else ""
    return icon, f"{sign}{diff:.1f}%"


def _pct(part, total) -> str:
    if not total:
        return "0%"
    return f"{part / total * 100:.1f}%"
