from __future__ import annotations


def format_anomaly_alert(anomalies: list[dict], row: dict, last_week_row: dict | None = None) -> str:
    date = row["report_date"]
    slot = row["hour_slot"]
    total_orders = int(row.get("total_order") or 0)
    basket = float(row.get("basket_size") or 0)
    gmv = total_orders * basket

    lines = [f"⚠️ *Sales Anomaly — {date} {slot}*", ""]
    lines.append(f"Orders:  *{total_orders:,}*")
    lines.append(f"GMV:     *HKD {gmv:,.0f}*")
    lines.append(f"Basket:  *HKD {basket:.2f}*")

    lines.append("\n*Anomalous metrics (vs 4-wk avg):*")
    for a in anomalies:
        icon = "📈" if a["direction"] == "high" else "📉"
        sign = "+" if a["pct_diff"] > 0 else ""
        current = _fmt(a["current"])
        avg = _fmt(a["mean"])
        lines.append(
            f"{icon} *{a['label']}*: {current} "
            f"({sign}{a['pct_diff']}% vs avg {avg}, Z={a['z_score']})"
        )

    if last_week_row:
        lw_orders = int(last_week_row.get("total_order") or 0)
        lw_basket = float(last_week_row.get("basket_size") or 0)
        lw_gmv = lw_orders * lw_basket
        icon_o, diff_o = _compare(total_orders, lw_orders)
        icon_g, diff_g = _compare(gmv, lw_gmv)
        lines.append(f"\n{icon_o} vs last week same hour: {lw_orders:,} orders ({diff_o}), HKD {lw_gmv:,.0f} GMV ({diff_g})")

    return "\n".join(lines)


def format_halfday_summary(data: dict, report_date: str) -> str:
    weekday = data["weekday_name"]
    today_orders = data["today_orders"]
    today_gmv = data["today_gmv"]
    avg_basket = data["today_avg_basket"]
    lw_orders = data["lw_orders"]
    lw_gmv = data["lw_gmv"]
    fw_orders = data["fw_avg_orders"]
    fw_gmv = data["fw_avg_gmv"]

    lines = [f"🌅 *Half-Day Summary — {weekday} {report_date}*", "Period: 00:00 – 12:00", ""]
    lines.append(f"Total Orders:    *{today_orders:,}*")
    lines.append(f"Total Revenue:   *HKD {today_gmv:,.0f}*")
    lines.append(f"Avg Order Value: *HKD {avg_basket:.2f}*")

    if lw_orders:
        icon, diff = _compare(today_orders, lw_orders)
        icon2, diff2 = _compare(today_gmv, lw_gmv)
        lines.append(f"\n{icon} vs last {weekday}: {lw_orders:,} orders ({diff}), HKD {lw_gmv:,.0f} ({diff2})")

    if fw_orders:
        icon, diff = _compare(today_orders, fw_orders)
        icon2, diff2 = _compare(today_gmv, fw_gmv)
        lines.append(f"{icon} vs 4-wk {weekday} avg: {fw_orders:,.0f} orders ({diff}), HKD {fw_gmv:,.0f} ({diff2})")

    if data.get("best_hour"):
        b, w = data["best_hour"], data["worst_hour"]
        lines.append(f"\n🏆 Best hour:    {b['hour_slot']} — {b['total_order']:,} orders (HKD {b['basket_size']:.0f} basket)")
        lines.append(f"🔻 Slowest hour: {w['hour_slot']} — {w['total_order']:,} orders (HKD {w['basket_size']:.0f} basket)")

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
