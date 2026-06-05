from __future__ import annotations

import base64
import json
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """This is a HKTVmall Daily Sales Update dashboard image. Extract metrics carefully by section.

LAYOUT GUIDE:
- TOP of image: Overall platform metrics with the LARGEST numbers (GMV in tens of millions HKD, orders in tens of thousands)
- Middle sections: Express Total, HM Mart, HKTVmall Veggie — subsection breakdowns
- Lower sections: ThePlace, Mainland Merchant, igloo+, Insurance, Online Normal vs 3PL, Same Day Delivery

Return ONLY a valid JSON object with exactly these keys (use null if not visible):

{
  "report_date": "YYYY-MM-DD",
  "top_level": {
    "gmv": number,
    "mtd_gmv": number,
    "gmv_projection": number,
    "gmv_per_order": number,
    "net_sales": number,
    "mtd_net_sales": number,
    "num_orders": integer,
    "num_customers": integer,
    "conversion_rate": number
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
  "igloo_plus": {  // labeled "Plus+" or "igloo+" in the image
    "yesterday_gmv": number,
    "yesterday_orders": integer,
    "yesterday_gmv_per_order": number,
    "mtd_gmv": number,
    "mtd_orders": integer
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
    "yesterday_gmv_pct": number,
    "mtd_gmv_pct": number
  }
}

IMPORTANT rules:
- top_level GMV should be the TOTAL platform GMV (typically HKD 10M–30M range for a day), NOT a subsection
- top_level num_orders should be total platform orders (typically 30,000–50,000 range), NOT a subsection
- ThePlace, Mainland, igloo+ are SUBSECTIONS with much smaller numbers
- All monetary values in HKD — remove $ signs and commas
- Percentages as plain numbers (e.g. 14.8 not 0.148)
- Return only the JSON, no explanation"""


def parse_daily_dashboard(image_bytes: bytes) -> dict | None:
    """Send image to Claude Vision and extract structured metrics."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    # Detect image type from magic bytes
    media_type = "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        media_type = "image/jpeg"
    elif image_bytes[:4] == b"\x89PNG":
        media_type = "image/png"
    elif image_bytes[:4] == b"GIF8":
        media_type = "image/gif"
    elif image_bytes[:4] == b"RIFF":
        media_type = "image/webp"

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": _EXTRACTION_PROMPT},
                    ],
                }
            ],
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        # Strip markdown code fences if present
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
        log.error("Claude Vision extraction failed: %s", exc)
        return None
