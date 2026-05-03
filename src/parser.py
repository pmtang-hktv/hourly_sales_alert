from __future__ import annotations

import re

# Ordered to match column positions in the email table (columns 4–24)
GATEWAY_COLS = [
    "asiapay", "globalpay", "mpgs", "amex", "paypal", "payme",
    "octopus", "atome", "unionpay", "alipayhk", "wechatpay", "wechatmp",
    "bocpay", "citicobrand", "mpay", "applepay", "mfood", "ctm", "fps",
    "pos_offline", "pos_online",
]


def parse_email(subject: str, body: str) -> dict | None:
    """
    Returns:
      report_date  – YYYY-MM-DD of the period start (the day being reported)
      email_hour   – int hour from the subject bracket, 0 = midnight/full-day email
      summary      – dict of daily totals (only present in 00:00 email)
      rows         – list of per-hour-slot dicts ready for DB insertion
    """
    m = re.search(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}):00\]", subject)
    if not m:
        return None
    email_hour = int(m.group(2))

    # "from YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM" — use the start date as report_date
    m2 = re.search(
        r"Payment gateway statistics from (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}"
        r" to (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}",
        body,
    )
    if not m2:
        return None
    report_date = m2.group(1)

    summary = _parse_summary(body)
    rows = _parse_table(body, report_date)

    return {
        "report_date": report_date,
        "email_hour": email_hour,
        "summary": summary,
        "rows": rows,
    }


def _parse_summary(body: str) -> dict:
    out = {}
    patterns = [
        ("total_order_success",   r"Total Order Success:\s*([\d,]+)"),
        ("total_order_over_2000", r"Total Order Over \$2000:\s*([\d,]+)"),
        ("total_amount",          r"Total Amount:\s*\$([\d,]+\.?\d*)"),
        ("avg_amount_per_order",  r"Average Amount Per Order:\s*\$([\d,]+\.?\d*)"),
    ]
    for key, pattern in patterns:
        m = re.search(pattern, body)
        if m:
            val = m.group(1).replace(",", "")
            out[key] = float(val) if "." in val else int(val)
    return out


def _parse_table(body: str, report_date: str) -> list[dict]:
    lines = body.splitlines()

    # Find the header row
    header_idx = next(
        (i for i, l in enumerate(lines) if l.strip().startswith("Time") and "Total Order" in l),
        None,
    )
    if header_idx is None:
        return []

    rows = []
    for line in lines[header_idx + 1:]:
        line = line.strip()
        if not line or not re.match(r"\d{2}-\d{2}", line):
            continue

        # Try tab-split first, fall back to multi-space split
        parts = re.split(r"\t+", line)
        if len(parts) < 25:
            parts = re.split(r" {2,}", line)
        if len(parts) < 25:
            continue

        try:
            hour_slot = parts[0].strip()          # "00-01"
            hour_start = int(hour_slot.split("-")[0])
            row: dict = {
                "report_date":   report_date,
                "hour_slot":     hour_slot,
                "hour_start":    hour_start,
                "total_order":   int(parts[1]),
                "total_payment": int(parts[2]),
            }
            for i, gw in enumerate(GATEWAY_COLS):
                row[gw] = int(parts[3 + i]) if (3 + i) < len(parts) else 0
            basket_str = parts[24].replace("$", "").replace(",", "")
            row["basket_size"] = float(basket_str) if basket_str else 0.0
            rows.append(row)
        except (ValueError, IndexError):
            continue

    return rows
