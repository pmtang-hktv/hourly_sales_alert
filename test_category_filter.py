"""Check what pre-applied filters are limiting the bycat view, and try overriding them.

Run: python3 test_category_filter.py [YYYY-MM-DD]
"""
from __future__ import annotations
import sys, urllib3
from datetime import datetime, timedelta
import requests
from src.config import TABLEAU_SERVER, TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_API = "3.15"
_WORKBOOK = "MonthlySalesbystore"
_VIEW = "bycat"
DATE = sys.argv[1] if len(sys.argv) > 1 else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def signin():
    r = requests.post(f"{TABLEAU_SERVER}/api/{_API}/auth/signin",
        json={"credentials": {"personalAccessTokenName": TABLEAU_PAT2_NAME,
              "personalAccessTokenSecret": TABLEAU_PAT2_SECRET, "site": {"contentUrl": ""}}},
        headers={"content-type": "application/json", "accept": "application/json"},
        timeout=30, verify=False)
    r.raise_for_status()
    c = r.json()["credentials"]
    return c["token"], c["site"]["id"]


def get_view_id(base, hdrs, site_id):
    r = requests.get(f"{base}/sites/{site_id}/workbooks", headers=hdrs,
        params={"filter": f"contentUrl:eq:{_WORKBOOK}"}, timeout=30, verify=False)
    r.raise_for_status()
    wb_id = r.json()["workbooks"]["workbook"][0]["id"]
    r = requests.get(f"{base}/sites/{site_id}/workbooks/{wb_id}/views", headers=hdrs,
        timeout=30, verify=False)
    r.raise_for_status()
    views = r.json()["views"]["view"]
    return next(v["id"] for v in views if v["viewUrlName"] == _VIEW)


def fetch(base, hdrs, site_id, view_id, params, label):
    r = requests.get(f"{base}/sites/{site_id}/views/{view_id}/data",
        headers=hdrs, params=params, timeout=120, verify=False)
    lines = r.text.splitlines()
    # Sum GMV from "All" rows (month == "All") across all category rows
    import csv, io
    total_gmv = 0.0
    rows = 0
    for row in csv.DictReader(io.StringIO(r.text)):
        if row.get("Measure Names") == "GMV" and row.get("Month of Order Date (day)") == "All":
            if not any((row.get(f"Primary Sub Cat {i} Code") or "").strip() for i in range(1,5)):
                # grand total row
                try: total_gmv = float(row.get("Measure Values") or 0)
                except: pass
            rows += 1
    print(f"\n[{label}]")
    print(f"  params: {params}")
    print(f"  HTTP {r.status_code} | csv rows: {len(lines)} | grand total GMV: {total_gmv:,.0f}")


def main():
    base = f"{TABLEAU_SERVER}/api/{_API}"
    token, site_id = signin()
    hdrs = {"x-tableau-auth": token, "accept": "application/json"}
    view_id = get_view_id(base, hdrs, site_id)
    print(f"View id: {view_id}  |  Target date: {DATE}")

    # Baseline — only date filter (view's own sales_application filter active)
    fetch(base, hdrs, site_id, view_id,
          {"vf_Order Date (day)": DATE},
          "baseline (view default filters)")

    # Try overriding sales_application to All
    fetch(base, hdrs, site_id, view_id,
          {"vf_Order Date (day)": DATE, "vf_sales_application": "All"},
          "sales_application=All")

    # Try including specific apps
    for app in ["Web", "WebMobile", "CTM", "MFOOD", "QCS", "WECHAT"]:
        fetch(base, hdrs, site_id, view_id,
              {"vf_Order Date (day)": DATE, "vf_sales_application": app},
              f"sales_application={app}")

    requests.post(f"{base}/auth/signout", headers=hdrs, timeout=10, verify=False)


if __name__ == "__main__":
    main()
