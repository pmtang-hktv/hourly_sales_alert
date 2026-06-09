from __future__ import annotations

import base64
import json
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """This is a HKTVmall Daily Sales Update PDF report. Extract all metrics carefully.

Return ONLY a valid JSON object with exactly these keys (use null if not found):

{
  "report_date": "YYYY-MM-DD (dates in report are D/M/YYYY HK format — convert correctly, e.g. 8/6/2026 = 2026-06-08)",
  "top_level": {
    "gmv": number,
    "mtd_gmv": number,
    "gmv_projection": number,
    "gmv_per_order": number,
    "mtd_gmv_per_order": number,
    "net_sales": number,
    "mtd_net_sales": number,
    "net_sales_projection": number,
    "num_orders": integer,
    "num_customers": integer,
    "new_customers": integer,
    "mtd_new_customers": integer,
    "mtd_num_customers": integer
  },
  "mainland_merchant": {
    "yesterday_gmv": number,
    "yesterday_orders": integer,
    "yesterday_customers": integer,
    "yesterday_gmv_per_order": number,
    "mtd_gmv": number,
    "mtd_orders": integer,
    "mtd_customers": integer,
    "mtd_gmv_per_order": number
  },
  "igloo_plus": {
    "yesterday_gmv": number,
    "yesterday_orders": integer,
    "yesterday_customers": integer,
    "yesterday_gmv_per_order": number,
    "mtd_gmv": number,
    "mtd_orders": integer,
    "mtd_customers": integer,
    "mtd_gmv_per_order": number
  },
  "theplace": {
    "yesterday_gmv": number,
    "yesterday_orders": integer,
    "yesterday_customers": integer,
    "yesterday_gmv_per_order": number,
    "mtd_gmv": number,
    "mtd_orders": integer,
    "mtd_customers": integer,
    "mtd_gmv_per_order": number
  },
  "insurance": {
    "yesterday_gmv": number,
    "yesterday_orders": integer,
    "mtd_gmv": number,
    "mtd_orders": integer
  },
  "online_normal_vs_3pl": {
    "yesterday_normal_gmv": number,
    "yesterday_3pl_gmv": number,
    "yesterday_normal_pct": number,
    "yesterday_3pl_pct": number,
    "mtd_normal_gmv": number,
    "mtd_3pl_gmv": number,
    "mtd_normal_pct": number,
    "mtd_3pl_pct": number
  },
  "same_day_delivery": {
    "yesterday_gmv": number,
    "yesterday_gmv_pct": number,
    "mtd_gmv": number,
    "mtd_gmv_pct": number
  }
}

Rules:
- top_level GMV is the TOTAL platform yesterday GMV (tens of millions HKD), not a subsection
- top_level num_orders is total platform yesterday orders (tens of thousands), not a subsection
- All monetary values in HKD — strip $ signs and commas
- Percentages as plain numbers (e.g. 54.7 not 0.547)
- Return only the JSON, no explanation"""


def parse_daily_dashboard(pdf_bytes: bytes) -> dict | None:
    """Send the Daily Sales Update PDF to Claude and extract structured metrics."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    try:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": _EXTRACTION_PROMPT},
                    ],
                }
            ],
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        data = json.loads(text)
        log.info("Daily dashboard parsed successfully")
        return data

    except json.JSONDecodeError as exc:
        log.error("Failed to parse JSON from Claude response: %s", exc)
        return None
    except Exception as exc:
        log.error("Claude PDF extraction failed: %s", exc)
        return None
