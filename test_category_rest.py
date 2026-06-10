"""Diagnostic: can we pull Category Performance (GMV by cat) via REST API with the
second PAT account, and can we filter it down to a single day?

Run:  python3 test_category_rest.py [YYYY-MM-DD]

Signs in with TABLEAU_PAT2_NAME/SECRET, locates the MonthlySalesbystore / bycat
view, then downloads the view's summary data CSV:
  1. with NO filter (shows the natural shape + columns)
  2. with a date filter, trying several candidate field names + formats

For each attempt it prints row count, byte size, and the first few lines so we can
see which filter actually narrows the data to the target day.
"""
from __future__ import annotations

import sys
import urllib3
from datetime import datetime, timedelta

import requests

from src.config import TABLEAU_SERVER, TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_API = "3.15"
_WORKBOOK = "MonthlySalesbystore"   # contentUrl from .../#/views/MonthlySalesbystore/bycat
_VIEW = "bycat"                      # viewUrlName


def _signin(base: str) -> tuple[str, str]:
    resp = requests.post(
        f"{base}/auth/signin",
        json={"credentials": {
            "personalAccessTokenName": TABLEAU_PAT2_NAME,
            "personalAccessTokenSecret": TABLEAU_PAT2_SECRET,
            "site": {"contentUrl": ""},
        }},
        headers={"content-type": "application/json", "accept": "application/json"},
        timeout=30, verify=False,
    )
    resp.raise_for_status()
    creds = resp.json()["credentials"]
    return creds["token"], creds["site"]["id"]


def _find_view(base: str, headers: dict, site_id: str) -> str:
    resp = requests.get(
        f"{base}/sites/{site_id}/workbooks",
        headers=headers, params={"filter": f"contentUrl:eq:{_WORKBOOK}"},
        timeout=30, verify=False,
    )
    resp.raise_for_status()
    wbs = resp.json().get("workbooks", {}).get("workbook", [])
    if not wbs:
        raise SystemExit(f"No workbook with contentUrl={_WORKBOOK} (does PAT2 have access?)")
    wb_id = wbs[0]["id"]
    print(f"Workbook: id={wb_id} name={wbs[0].get('name')}")

    resp = requests.get(
        f"{base}/sites/{site_id}/workbooks/{wb_id}/views",
        headers=headers, timeout=30, verify=False,
    )
    resp.raise_for_status()
    views = resp.json().get("views", {}).get("view", [])
    print("Views in workbook:", [v.get("viewUrlName") for v in views])
    view = next((v for v in views if v.get("viewUrlName") == _VIEW), None)
    if not view:
        raise SystemExit(f"No view '{_VIEW}' in workbook")
    print(f"View: id={view['id']} name={view.get('name')}")
    return view["id"]


def _download_data(base: str, headers: dict, site_id: str, view_id: str,
                   params: dict, label: str):
    resp = requests.get(
        f"{base}/sites/{site_id}/views/{view_id}/data",
        headers=headers, params=params, timeout=120, verify=False,
    )
    print(f"\n===== {label} =====")
    print("URL params:", params)
    print("HTTP", resp.status_code)
    if not resp.ok:
        print("ERROR body:", resp.text[:400])
        return
    text = resp.text
    lines = text.splitlines()
    print(f"bytes={len(text)} rows={len(lines)}")
    for ln in lines[:6]:
        print("  ", ln[:300])


def main():
    if not (TABLEAU_PAT2_NAME and TABLEAU_PAT2_SECRET):
        raise SystemExit("Set TABLEAU_PAT2_NAME and TABLEAU_PAT2_SECRET in .env first")

    day = sys.argv[1] if len(sys.argv) > 1 else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Target day: {day}")

    base = f"{TABLEAU_SERVER}/api/{_API}"
    token, site_id = _signin(base)
    headers = {"x-tableau-auth": token, "accept": "application/json"}
    print(f"Signed in (site_id={site_id})")

    view_id = _find_view(base, headers, site_id)

    # 1. No filter — baseline shape
    _download_data(base, headers, site_id, view_id, {}, "NO FILTER (baseline)")

    # 2. Try candidate date-filter field names / formats. Whichever changes the
    #    row count or values vs baseline is the one that works.
    candidates = [
        {f"vf_Order Date (day)": day},
        {f"vf_Order Date": day},
        {f"vf_order_date": day},
        {f"vf_ORDER_DATE": day},
        {f"vf_Order Date (day)": day.replace("-", "/")},   # 2026/06/09
        {f"vf_Order Date": day.replace("-", "/")},
    ]
    for c in candidates:
        _download_data(base, headers, site_id, view_id, c, f"FILTER {c}")

    requests.post(f"{base}/auth/signout", headers=headers, timeout=10, verify=False)
    print("\nDone. Look for the attempt whose rows/values reflect a single day.")


if __name__ == "__main__":
    main()
