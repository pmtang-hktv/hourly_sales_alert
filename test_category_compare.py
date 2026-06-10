"""Compare REST extraction methods against the manual 15.98M figure for one day.

Run: python3 test_category_compare.py [YYYY-MM-DD]

Downloads the bycat view two ways and reports grand-total GMV for each:
  A) /views/{id}/data           (summary CSV — what we used, gave 473K)
  B) /views/{id}/crosstab/excel (the exact file you download manually)

Saves the crosstab to data/category/_probe_bycat_<date>.xlsx for inspection.
"""
from __future__ import annotations
import sys, os, io, csv, urllib3
from datetime import datetime, timedelta
import requests
from src.config import TABLEAU_SERVER, TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET, CATEGORY_DIR

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_API = "3.15"
_WB = "MonthlySalesbystore"
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
        params={"filter": f"contentUrl:eq:{_WB}"}, timeout=30, verify=False)
    r.raise_for_status()
    wb_id = r.json()["workbooks"]["workbook"][0]["id"]
    r = requests.get(f"{base}/sites/{site_id}/workbooks/{wb_id}/views", headers=hdrs,
        timeout=30, verify=False)
    r.raise_for_status()
    return next(v["id"] for v in r.json()["views"]["view"] if v["viewUrlName"] == _VIEW)


def probe_data_csv(base, hdrs, site_id, vid):
    r = requests.get(f"{base}/sites/{site_id}/views/{vid}/data",
        headers=hdrs, params={"vf_Order Date (day)": DATE}, timeout=120, verify=False)
    rows = list(csv.DictReader(io.StringIO(r.text)))
    gmv_all = [x for x in rows if x.get("Measure Names") == "GMV"]
    # grand total = GMV row with all cat codes blank, month != All (single month col)
    grand = 0.0
    for x in gmv_all:
        codes = [(x.get(f"Primary Sub Cat {i} Code") or "").strip() for i in range(1, 5)]
        if not any(codes) and x.get("Month of Order Date (day)") != "All":
            try: grand = max(grand, float(x.get("Measure Values") or 0))
            except: pass
    # sum of leaf marks (deepest non-empty code), month != All
    leaf_sum = 0.0
    for x in gmv_all:
        if x.get("Month of Order Date (day)") == "All":
            continue
        codes = [(x.get(f"Primary Sub Cat {i} Code") or "").strip() for i in range(1, 5)]
        if any(codes):
            try: leaf_sum += float(x.get("Measure Values") or 0)
            except: pass
    print(f"\n[A] /data CSV: total csv rows={len(rows)} GMV rows={len(gmv_all)}")
    print(f"    grand-total row (all codes blank): {grand:,.0f}")
    print(f"    sum of all GMV rows w/ a code:     {leaf_sum:,.0f}")


def probe_crosstab(base, hdrs, site_id, vid):
    import openpyxl
    r = requests.get(f"{base}/sites/{site_id}/views/{vid}/crosstab/excel",
        headers=hdrs, params={"vf_Order Date (day)": DATE}, timeout=180, verify=False)
    print(f"\n[B] /crosstab/excel: HTTP {r.status_code} bytes={len(r.content)}")
    if not r.ok:
        print("    ERROR:", r.text[:300]); return
    os.makedirs(CATEGORY_DIR, exist_ok=True)
    path = os.path.join(CATEGORY_DIR, f"_probe_bycat_{DATE}.xlsx")
    with open(path, "wb") as f:
        f.write(r.content)
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    print(f"    saved {path}  dims={ws.max_row}x{ws.max_column}")
    # main-cat subtotal sum: rows where col3=='Total' and col1 blank
    main_sub = 0.0
    for rr in range(3, ws.max_row + 1):
        c1 = ws.cell(rr, 1).value
        c3 = ws.cell(rr, 3).value
        gmv = ws.cell(rr, 9).value or 0
        if c3 == "Total" and (c1 is None or c1 == ""):
            if isinstance(gmv, (int, float)):
                main_sub += gmv
    print(f"    sum of main-cat subtotal rows (col9 GMV): {main_sub:,.0f}")


def main():
    base = f"{TABLEAU_SERVER}/api/{_API}"
    token, site_id = signin()
    hdrs = {"x-tableau-auth": token, "accept": "application/json"}
    vid = get_view_id(base, hdrs, site_id)
    print(f"View id={vid}  date={DATE}  (manual figure to match: 15,984,570)")
    probe_data_csv(base, hdrs, site_id, vid)
    probe_crosstab(base, hdrs, site_id, vid)
    requests.post(f"{base}/auth/signout", headers=hdrs, timeout=10, verify=False)


if __name__ == "__main__":
    main()
