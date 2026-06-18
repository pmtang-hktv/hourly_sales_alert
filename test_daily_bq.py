"""Test downloading the DailySalesUpdate-BQ workbook views as PDF via Tableau REST API.

This is a READ-ONLY probe — it does NOT touch the database or deploy anything.
It tries both configured PATs (TABLEAU_PAT and TABLEAU_PAT2), finds the workbook,
lists every view it can see, then attempts a PDF download of each target view and
reports the result (HTTP status + file size).

Run on the Mac (the sandbox cannot reach the internal Tableau server):
  python3 test_daily_bq.py            # download yesterday (default view date)
  python3 test_daily_bq.py 2026-06-15 # force a specific Order Date (day) filter
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from src.config import (
    TABLEAU_SERVER,
    TABLEAU_PAT_NAME, TABLEAU_PAT_SECRET,
    TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("test_daily_bq")

_API_VERSION = "3.15"
_WORKBOOK = "DailySalesUpdate-BQ"
_VIEWS = [
    "DailySalesUpdate_1",
    "SalesReportDaily",
    "SalesbyTeam",
    "SalesbyProduct",
    "FulfillmentOverview",
    "OrderbyHourinPast8days",
]
_OUT_DIR = "data/daily_bq_test"


def _signin(base: str, name: str, secret: str) -> tuple[str, str]:
    resp = requests.post(
        f"{base}/auth/signin",
        json={"credentials": {
            "personalAccessTokenName": name,
            "personalAccessTokenSecret": secret,
            "site": {"contentUrl": ""},
        }},
        headers={"content-type": "application/json", "accept": "application/json"},
        timeout=30, verify=False,
    )
    resp.raise_for_status()
    creds = resp.json()["credentials"]
    return creds["token"], creds["site"]["id"]


def _find_workbook(base: str, headers: dict, site_id: str) -> str | None:
    resp = requests.get(
        f"{base}/sites/{site_id}/workbooks",
        headers=headers,
        params={"filter": f"contentUrl:eq:{_WORKBOOK}"},
        timeout=30, verify=False,
    )
    resp.raise_for_status()
    wbs = resp.json().get("workbooks", {}).get("workbook", [])
    return wbs[0]["id"] if wbs else None


def _list_views(base: str, headers: dict, site_id: str, wb_id: str) -> list[dict]:
    resp = requests.get(
        f"{base}/sites/{site_id}/workbooks/{wb_id}/views",
        headers=headers, timeout=30, verify=False,
    )
    resp.raise_for_status()
    return resp.json().get("views", {}).get("view", [])


def _download_pdf(base: str, headers: dict, site_id: str, view_id: str,
                  date_str: str | None) -> tuple[int, bytes]:
    params = {}
    if date_str:
        params["vf_Order Date (day)"] = date_str
    resp = requests.get(
        f"{base}/sites/{site_id}/views/{view_id}/pdf",
        headers=headers, params=params, timeout=180, verify=False,
    )
    return resp.status_code, resp.content


def _try_with_pat(label: str, name: str, secret: str, date_str: str | None) -> bool:
    if not name or not secret:
        log.info("[%s] not configured — skipping", label)
        return False

    base = f"{TABLEAU_SERVER}/api/{_API_VERSION}"
    log.info("=" * 70)
    log.info("[%s] Signing in...", label)
    try:
        token, site_id = _signin(base, name, secret)
    except Exception as exc:
        log.error("[%s] sign-in failed: %s", label, exc)
        return False
    headers = {"x-tableau-auth": token, "accept": "application/json"}

    try:
        wb_id = _find_workbook(base, headers, site_id)
        if not wb_id:
            log.warning("[%s] workbook '%s' NOT visible to this PAT", label, _WORKBOOK)
            return False
        log.info("[%s] Found workbook '%s' (id=%s)", label, _WORKBOOK, wb_id)

        views = _list_views(base, headers, site_id, wb_id)
        available = {v.get("viewUrlName"): v["id"] for v in views}
        log.info("[%s] Views in workbook: %s", label, sorted(available.keys()))

        os.makedirs(_OUT_DIR, exist_ok=True)
        ok_count = 0
        for vname in _VIEWS:
            vid = available.get(vname)
            if not vid:
                log.warning("  [%s] view '%s' NOT found", label, vname)
                continue
            try:
                status, content = _download_pdf(base, headers, site_id, vid, date_str)
                if status == 200 and content[:4] == b"%PDF":
                    path = os.path.join(_OUT_DIR, f"{vname}.pdf")
                    with open(path, "wb") as f:
                        f.write(content)
                    log.info("  [%s] %-26s OK  %8d bytes -> %s", label, vname, len(content), path)
                    ok_count += 1
                else:
                    log.warning("  [%s] %-26s FAILED status=%s len=%d (not a PDF)",
                                label, vname, status, len(content))
                    log.warning("       response head: %s", content[:200])
            except Exception as exc:
                log.error("  [%s] %-26s ERROR %s", label, vname, exc)

        log.info("[%s] Downloaded %d / %d views as PDF", label, ok_count, len(_VIEWS))
        return ok_count > 0
    finally:
        try:
            requests.post(f"{base}/auth/signout", headers=headers, timeout=10, verify=False)
        except Exception:
            pass


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else None
    if date_str:
        log.info("Using Order Date (day) filter = %s", date_str)
    else:
        log.info("No date filter — views will show their default (usually yesterday)")
        log.info("(yesterday would be %s)", (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))

    got_pat = _try_with_pat("PAT (TABLEAU_PAT)", TABLEAU_PAT_NAME, TABLEAU_PAT_SECRET, date_str)
    got_pat2 = _try_with_pat("PAT2 (TABLEAU_PAT2)", TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET, date_str)

    log.info("=" * 70)
    if got_pat or got_pat2:
        which = "PAT" if got_pat else "PAT2"
        log.info("SUCCESS — at least one existing token works (%s). No new PAT needed.", which)
        log.info("PDFs saved under %s/ — open them to confirm they match the dashboards.", _OUT_DIR)
    else:
        log.info("NEITHER existing PAT could download from '%s'.", _WORKBOOK)
        log.info("You likely need a PAT whose account has access to this workbook.")


if __name__ == "__main__":
    main()
