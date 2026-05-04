from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime

import anthropic
import requests

from src.config import ANTHROPIC_API_KEY, DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

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

Guidelines:
- Run SELECT queries only — never INSERT / UPDATE / DELETE
- Always check what date range is available before concluding data is missing
- If daily_summary has no data for a period, re-query using hourly_stats aggregates
- Format numbers with commas (e.g. 12,345)
- Prefix monetary values with HKD (e.g. HKD 410.82)
- Keep answers brief: lead with the direct answer, then add context if useful
- If data truly does not exist after trying both tables, say so clearly
- Today's date is provided in each user message""",
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

    for _ in range(5):  # max 5 tool-call rounds
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
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


def _send_reply(text: str, markdown: bool = False):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if markdown:
        payload["parse_mode"] = "Markdown"
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            log.error("Telegram rejected message: %s", data)
    except Exception as exc:
        log.error("Failed to send reply: %s", exc)


def _handle_question(text: str):
    try:
        answer = answer_question(text)
        _send_reply(answer)
    except Exception as exc:
        log.error("Error answering: %s", exc)
        _send_reply(f"Sorry, something went wrong: {exc}")


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

                if chat_id != str(TELEGRAM_CHAT_ID) or not text:
                    continue
                if text.startswith("/"):
                    continue

                log.info("Question from %s: %s", chat_id, text)
                _send_reply("_Looking it up..._", markdown=True)
                threading.Thread(
                    target=_handle_question,
                    args=(text,),
                    daemon=True,
                ).start()

        except Exception as exc:
            log.error("Poll loop error: %s", exc)
            time.sleep(5)


def start_bot_listener():
    t = threading.Thread(target=_poll_loop, daemon=True, name="bot-listener")
    t.start()
    return t
