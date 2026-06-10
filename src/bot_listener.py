from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime

import anthropic
import requests

from src.config import ANTHROPIC_API_KEY, DB_PATH, TELEGRAM_ALL_CHAT_IDS, TELEGRAM_BOT_TOKEN

log = logging.getLogger(__name__)

# Stable schema description — cached by Claude API across calls
_SCHEMA = """
HKTVmall sales SQLite database (Hong Kong, HKD currency):

Table: hourly_stats  — one row per (report_date, hour_slot)
  report_date  TEXT  YYYY-MM-DD
  hour_slot    TEXT  e.g. "00-01", "13-14"
  hour_start   INTEGER  0-23
  total_order  INTEGER  number of orders placed
  total_payment INTEGER  number of payment transactions (can exceed total_order — multi-payment)
  basket_size  REAL  average order value in HKD
  Payment gateway columns (all INTEGER, transaction counts):
    asiapay, globalpay, mpgs, amex, paypal, payme, octopus, atome,
    unionpay, alipayhk, wechatpay, wechatmp, bocpay, citicobrand,
    mpay, applepay, mfood, ctm, fps, pos_offline, pos_online

Table: daily_summary  — one row per report_date (from midnight email)
  report_date           TEXT  YYYY-MM-DD
  total_order_success   INTEGER
  total_order_over_2000 INTEGER  orders with value > HKD 2000
  total_amount          REAL  total revenue in HKD
  avg_amount_per_order  REAL  HKD

Table: daily_dashboard  — one row per report_date (from morning dashboard email, extracted via Vision)
  report_date           TEXT  YYYY-MM-DD
  gmv, mtd_gmv, gmv_projection, gmv_per_order  REAL  HKD
  net_sales, mtd_net_sales  REAL  HKD
  num_orders            INTEGER
  num_customers         INTEGER
  conversion_rate       REAL  percentage
  mainland_yesterday_gmv, mainland_yesterday_orders, mainland_yesterday_customers,
    mainland_yesterday_gmv_per_order, mainland_mtd_gmv, mainland_mtd_orders
  igloo_yesterday_gmv, igloo_yesterday_orders, igloo_mtd_gmv, igloo_mtd_orders
  theplace_yesterday_gmv, theplace_yesterday_orders, theplace_yesterday_customers,
    theplace_yesterday_gmv_per_order, theplace_mtd_gmv, theplace_mtd_orders
  insurance_yesterday_gmv, insurance_yesterday_orders, insurance_mtd_gmv, insurance_mtd_orders
  normal_yesterday_gmv, normal_yesterday_pct, threpl_yesterday_gmv, threpl_yesterday_pct  (Online Normal vs 3PL)
  normal_mtd_gmv, normal_mtd_pct, threpl_mtd_gmv, threpl_mtd_pct
  sameday_yesterday_pct, sameday_mtd_pct  REAL  percentage of GMV from same-day delivery merchants

Table: category_performance  — one row per (report_date, category leaf) from the daily Tableau export
  report_date  TEXT  YYYY-MM-DD
  main_cat     TEXT  top-level category (Traditional Chinese), e.g. 水果 蔬菜 鮮花, 冷凍/急凍食品
  sub_cat1, sub_cat2, sub_cat3, sub_cat4  TEXT  sub-category levels (Chinese; '' if not applicable)
  leaf_cat     TEXT  the deepest non-empty category name for this row
  level        INTEGER  depth of leaf (1=main … 4=sub_cat3; sub_cat4 is currently always '')
  gmv          REAL  gross merchandise value in HKD for this leaf category
  gp           REAL  gross profit in HKD — NOT AVAILABLE: always 0 (the GMV source view has no GP column)
  gp_pct       REAL  gross profit margin — NOT AVAILABLE: always 0
  Notes: each row is a distinct leaf — summing gmv across rows gives the total (no subtotal rows).
  To get main-category totals: SUM(gmv) GROUP BY main_cat.
  Category names are in Traditional Chinese — match user terms to these (e.g. "supermarket"=超級市場).
  IMPORTANT: gp and gp_pct are always 0 — do NOT answer gross-profit or margin questions
  from this table. If asked about category GP/margin, say it is not currently available.

Table: store_performance  — one row per (report_date, store_code, main_cat) from daily Tableau export
  report_date  TEXT  YYYY-MM-DD
  store_code   TEXT  merchant store ID (e.g. 'H0888001')
  store_name   TEXT  store display name
  rm_code      TEXT  relationship manager code (e.g. 'B0079')
  rm_name      TEXT  RM full name
  main_cat     TEXT  top-level product category (Traditional Chinese)
  gmv          REAL  gross merchandise value in HKD for this store × category combination
  customers    INTEGER  unique customers
  orders       INTEGER  parent orders
  Notes: to get per-store totals across all categories: SUM(gmv) GROUP BY store_code.
  To get RM totals: SUM(gmv) GROUP BY rm_code (or rm_name).
  To get store ranking: SUM(gmv) GROUP BY store_code ORDER BY SUM(gmv) DESC.
  No GP/margin data available in this table.
  store_performance is populated once daily at ~15:30 HKT — today's data may not exist yet.
"""

_SYSTEM = [
    {
        "type": "text",
        "text": f"""You are a sales data analyst for HKTVmall. Answer questions about sales \
by querying the database with the query_sales_db tool, then giving a clear, \
concise answer in plain English.

{_SCHEMA}

Data availability note:
- daily_summary is only populated from midnight emails — it may not cover all dates
- hourly_stats covers more history; use it as a fallback when daily_summary has no data
- To get daily totals from hourly_stats: SUM(total_order) grouped by report_date
- To estimate daily revenue from hourly_stats: SUM(total_order * basket_size)
- category_performance is populated once daily at ~15:00 HKT — today's and yesterday's rows may not exist yet

Guidelines:
- Run SELECT queries only — never INSERT / UPDATE / DELETE
- Always check what date range is available (MIN/MAX report_date) before querying a specific period
- If a table has no rows for the requested date, say so immediately — do not retry the same query
- If daily_summary has no data for a period, re-query using hourly_stats aggregates
- Format numbers with commas (e.g. 12,345)
- Prefix monetary values with HKD (e.g. HKD 410.82)
- Keep answers brief: lead with the direct answer, then add context if useful
- Today's date is provided in each user message
- IMPORTANT: Use plain text only — no Markdown, no ** bold **, no ## headings, no | tables |, no --- dividers. Use simple line breaks and numbered/bulleted lists with hyphens instead.

For growth / anomaly questions:
- Define "surprising" as >= 20% change vs the same weekday average over available prior weeks
- Use SQL to compute per-category averages across prior weeks, then compare to the latest week
- If fewer than 2 prior weeks exist, say so and show raw figures instead""",
        "cache_control": {"type": "ephemeral"},
    }
]

_TOOLS = [
    {
        "name": "query_sales_db",
        "description": "Run a SQLite SELECT query against the HKTVmall sales database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A valid SQLite SELECT statement"}
            },
            "required": ["sql"],
        },
    }
]


def _run_sql(sql: str) -> str:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
        conn.close()
        if not rows:
            return "No rows returned."
        cols = list(rows[0].keys())
        lines = ["\t".join(cols)]
        for row in rows[:100]:
            lines.append("\t".join(str(row[c] if row[c] is not None else "") for c in cols))
        if len(rows) > 100:
            lines.append(f"(showing first 100 of {len(rows)} rows)")
        return "\n".join(lines)
    except Exception as exc:
        return f"SQL error: {exc}"


def answer_question(question: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today = datetime.now().strftime("%Y-%m-%d (%A)")

    messages: list[dict] = [
        {"role": "user", "content": f"Today: {today}\n\nQuestion: {question}"}
    ]

    for _ in range(10):  # max 10 tool-call rounds
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_SYSTEM,
            tools=_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return next(
                (b.text for b in response.content if b.type == "text"),
                "Sorry, I couldn't generate an answer.",
            )

        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                log.info("SQL: %s", block.input.get("sql", ""))
                result = _run_sql(block.input["sql"])
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
        messages.append({"role": "user", "content": tool_results})

    return "Sorry, I couldn't complete the query."


def _send_reply(text: str, chat_id: str, parse_html: bool = False):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": text}
    if parse_html:
        payload["parse_mode"] = "HTML"
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            log.error("Telegram rejected message: %s", data)
    except Exception as exc:
        log.error("Failed to send reply: %s", exc)


def _handle_question(text: str, chat_id: str):
    try:
        answer = answer_question(text)
        _send_reply(answer, chat_id)
    except Exception as exc:
        log.error("Error answering: %s", exc)
        _send_reply(f"Sorry, something went wrong: {exc}", chat_id)


def _poll_loop():
    offset: int | None = None
    log.info("Telegram bot listener started — ready for questions")
    while True:
        try:
            params: dict = {"timeout": 20, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params=params,
                timeout=(10, 25),
            )
            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = (msg.get("text") or "").strip()

                if chat_id not in [str(c) for c in TELEGRAM_ALL_CHAT_IDS] or not text:
                    continue
                if text.startswith("/"):
                    continue

                log.info("Question from %s: %s", chat_id, text)
                _send_reply("<i>Looking it up...</i>", chat_id, parse_html=True)
                threading.Thread(
                    target=_handle_question,
                    args=(text, chat_id),
                    daemon=True,
                ).start()

        except Exception as exc:
            log.error("Poll loop error: %s", exc)
            time.sleep(5)


def start_bot_listener():
    t = threading.Thread(target=_poll_loop, daemon=True, name="bot-listener")
    t.start()
    return t
