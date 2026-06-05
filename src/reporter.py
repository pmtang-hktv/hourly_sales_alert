from __future__ import annotations

from datetime import datetime


def _fmt_date(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%-d %b %Y")
    except ValueError:
        return date_str


def format_anomaly_alert(anomalies: list[dict], row: dict, last_week_row: dict | None = None, day_totals: dict | None = None) -> str:
    date = row["report_date"]
    slot = row["hour_slot"]
    total_orders = int(row.get("total_order") or 0)
    basket = float(row.get("basket_size") or 0)
    gmv = total_orders * basket

    lines = [f"⚠️ <b>Sales Anomaly — {date} {slot}</b>", ""]
    lines.append(f"Orders:  <b>{total_orders:,}</b>")
    lines.append(f"GMV:     <b>HKD {gmv:,.0f}</b>")
    lines.append(f"Basket:  <b>HKD {basket:.2f}</b>")

    if day_totals and day_totals.get("orders"):
        d_orders = int(day_totals["orders"] or 0)
        d_gmv = float(day_totals["gmv"] or 0)
        end_hour = int(day_totals.get("last_hour_start", row.get("hour_start", 0))) + 1
        lines.append(f"\n📅 Day so far (00:00–{end_hour:02d}:00): <b>{d_orders:,}</b> orders, <b>HKD {d_gmv:,.0f}</b> GMV")

    lines.append("\n<b>Anomalous metrics (vs 4-wk avg):</b>")
    for a in anomalies:
        icon = "📈" if a["direction"] == "high" else "📉"
        sign = "+" if a["pct_diff"] > 0 else ""
        current = _fmt(a["current"])
        avg = _fmt(a["mean"])
        lines.append(f"{icon} <b>{a['label']}</b>: {current} ({sign}{a['pct_diff']}% vs avg {avg}, Z={a['z_score']})")

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

    lines = [f"🌅 <b>Half-Day Summary — {weekday} {_fmt_date(report_date)}</b>", "Period: 00:00 – 12:00", ""]
    lines.append(f"Total Orders:    <b>{today_orders:,}</b>")
    lines.append(f"Total Revenue:   <b>HKD {today_gmv:,.0f}</b>")
    lines.append(f"Avg Order Value: <b>HKD {avg_basket:.2f}</b>")

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

    lines = [f"📊 <b>Daily Summary — {weekday} {_fmt_date(date)}</b>", ""]
    lines.append(f"Total Orders:      <b>{total_orders:,}</b>")
    lines.append(f"Total Revenue:     <b>HKD {total_amount:,.0f}</b>")
    lines.append(f"Avg Order Value:   <b>HKD {avg_order:.2f}</b>")
    lines.append(f"Orders >HKD 2,000: <b>{over_2k:,}</b> ({_pct(over_2k, total_orders)} of orders)")

    if lw:
        icon, diff = _compare(total_orders, lw["total_order_success"])
        icon2, diff2 = _compare(total_amount, lw["total_amount"])
        lines.append(f"\n{icon} vs last {weekday}: {lw['total_order_success']:,} orders ({diff}), HKD {lw['total_amount']:,.0f} ({diff2})")

    if fw_orders:
        icon, diff = _compare(total_orders, fw_orders)
        icon2, diff2 = _compare(total_amount, fw_amount)
        lines.append(f"{icon} vs 4-wk {weekday} avg: {fw_orders:,.0f} orders ({diff}), HKD {fw_amount:,.0f} ({diff2})")

    if data.get("best_hour"):
        b, w = data["best_hour"], data["worst_hour"]
        lines.append(f"\n🏆 Best hour:    {b['hour_slot']} — {b['total_order']:,} orders (HKD {b['basket_size']:.0f} basket)")
        lines.append(f"🔻 Slowest hour: {w['hour_slot']} — {w['total_order']:,} orders (HKD {w['basket_size']:.0f} basket)")

    if data.get("gw_shifts"):
        lines.append("\n💳 <b>Notable payment gateway shifts vs last week:</b>")
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


def format_daily_dashboard_summary(row: dict, report_date: str) -> str:
    def hkd(v) -> str:
        if v is None:
            return "N/A"
        return f"HKD {v:,.0f}"

    def num(v) -> str:
        if v is None:
            return "N/A"
        return f"{int(v):,}"

    def pct(v) -> str:
        if v is None:
            return "N/A"
        return f"{v:.1f}%"

    lines = [f"📊 <b>Daily Sales Update — {_fmt_date(report_date)}</b>", ""]

    lines.append("<b>Top Level</b>")
    lines.append(f"GMV:             <b>{hkd(row.get('gmv'))}</b>  (MTD: {hkd(row.get('mtd_gmv'))})")
    lines.append(f"GMV Projection:  {hkd(row.get('gmv_projection'))}")
    lines.append(f"Net Sales:       <b>{hkd(row.get('net_sales'))}</b>  (MTD: {hkd(row.get('mtd_net_sales'))})")
    lines.append(f"Orders:          <b>{num(row.get('num_orders'))}</b>")
    lines.append(f"Customers:       {num(row.get('num_customers'))}")
    lines.append(f"GMV/Order:       {hkd(row.get('gmv_per_order'))}")
    lines.append(f"Conversion Rate: {pct(row.get('conversion_rate'))}")

    mm_gmv = row.get("mainland_yesterday_gmv")
    mm_ord = row.get("mainland_yesterday_orders")
    if mm_gmv or mm_ord:
        lines.append("\n<b>Mainland Merchant Sales</b>")
        lines.append(f"Yesterday: {hkd(mm_gmv)}, {num(mm_ord)} orders  (MTD: {hkd(row.get('mainland_mtd_gmv'))}, {num(row.get('mainland_mtd_orders'))} orders)")

    ig_gmv = row.get("igloo_yesterday_gmv")
    if ig_gmv:
        lines.append("\n<b>Plus+ Sales</b>")
        lines.append(f"Yesterday: {hkd(ig_gmv)}, {num(row.get('igloo_yesterday_orders'))} orders  (MTD: {hkd(row.get('igloo_mtd_gmv'))}, {num(row.get('igloo_mtd_orders'))} orders)")

    tp_gmv = row.get("theplace_yesterday_gmv")
    if tp_gmv:
        lines.append("\n<b>ThePlace</b>")
        lines.append(f"Yesterday: {hkd(tp_gmv)}, {num(row.get('theplace_yesterday_orders'))} orders  (MTD: {hkd(row.get('theplace_mtd_gmv'))}, {num(row.get('theplace_mtd_orders'))} orders)")

    ins_gmv = row.get("insurance_yesterday_gmv")
    if ins_gmv:
        lines.append("\n<b>Insurance Sales</b>")
        lines.append(f"Yesterday: {hkd(ins_gmv)}, {num(row.get('insurance_yesterday_orders'))} orders  (MTD: {hkd(row.get('insurance_mtd_gmv'))}, {num(row.get('insurance_mtd_orders'))} orders)")

    n_pct = row.get("normal_yesterday_pct")
    p_pct = row.get("threpl_yesterday_pct")
    if n_pct is not None or p_pct is not None:
        lines.append("\n<b>Online Normal vs 3PL</b>")
        lines.append(f"Yesterday: Normal {pct(n_pct)} ({hkd(row.get('normal_yesterday_gmv'))})  /  3PL {pct(p_pct)} ({hkd(row.get('threpl_yesterday_gmv'))})")
        lines.append(f"MTD:       Normal {pct(row.get('normal_mtd_pct'))} ({hkd(row.get('normal_mtd_gmv'))})  /  3PL {pct(row.get('threpl_mtd_pct'))} ({hkd(row.get('threpl_mtd_gmv'))})")

    sd_pct = row.get("sameday_yesterday_pct")
    if sd_pct is not None:
        lines.append("\n<b>Same Day Delivery Merchant SKU GMV%</b>")
        lines.append(f"Yesterday: {pct(sd_pct)}  /  MTD: {pct(row.get('sameday_mtd_pct'))}")

    return "\n".join(lines)
