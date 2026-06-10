"""Automated downloads from Tableau Server.

Category Performance (GP Report):
  1. Log in, open GP Overview, enter Access Key, set date range
  2. Switch to Category Performance tab → Download > Crosstab > Excel
  3. Rename to category_performance_YYYY-MM-DD.xlsx

Daily Sales Update:
  1. Log in, open DailySalesUpdate view (auto-shows yesterday)
  2. Download > PDF
  3. Rename to daily_sales_YYYY-MM-DD.pdf
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from src.config import CATEGORY_DIR, DAILY_DIR, STORE_DIR, TABLEAU_ACCESS_KEY, TABLEAU_HEADLESS, TABLEAU_PAT_NAME, TABLEAU_PAT_SECRET, TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET, TABLEAU_PASSWORD, TABLEAU_SERVER, TABLEAU_USERNAME

log = logging.getLogger(__name__)

_WAIT = 30
_DOWNLOAD_TIMEOUT = 300
_WORKBOOK = "RMDashboard-GPReport"
_GP_OVERVIEW = "GPOverview"
_DAILY_WORKBOOK = "DailySalesUpdate-BQSimplfied"
_DAILY_SHEET = "DailySalesUpdate"
_CAT_GMV_WORKBOOK  = "MonthlySalesbystore"
_CAT_GMV_VIEW      = "bycat"
_STORE_GMV_VIEW    = "bystorebymaincat"



def _make_driver(download_dir: str | None = None) -> webdriver.Chrome:
    abs_dir = str(Path(download_dir or CATEGORY_DIR).resolve())
    opts = Options()
    # Return from driver.get() on DOMContentLoaded — Tableau keeps loading long after the page is usable
    opts.page_load_strategy = "eager"
    if TABLEAU_HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_experimental_option("prefs", {
        "download.default_directory": abs_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": False,
        # Force PDFs to download instead of opening in the browser viewer
        "plugins.always_open_pdf_externally": True,
    })
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def _wait_for_tableau(driver: webdriver.Chrome, extra: float = 2.0):
    """Wait for the Tableau viz to stop showing a loading spinner."""
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            still_loading = driver.execute_script(
                "return !!document.querySelector('.tab-viz-loading, .tabBootProgressBar, "
                ".tb-loading-indicator')"
            )
            if not still_loading:
                break
        except Exception:
            break
        time.sleep(1)
    time.sleep(extra)


def _login(driver: webdriver.Chrome, target_url: str | None = None):
    """Log in to Tableau. Navigate directly to target_url (e.g. the view) so Tableau
    redirects through signin and back — saves one extra page load vs going to /#/signin first."""
    dest = target_url or f"{TABLEAU_SERVER}/#/signin"
    driver.get(dest)

    f = WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.CSS_SELECTOR,
        "input#username, input[name='username'], input[autocomplete='username'], input[type='text']"
    )))
    f.clear()
    f.send_keys(TABLEAU_USERNAME)

    p = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR,
        "input#password, input[name='password'], input[type='password']"
    )))
    p.clear()
    p.send_keys(TABLEAU_PASSWORD)

    btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR,
        "button[type='submit'], button.signin-btn, #signin-btn, input[type='submit']"
    )))
    btn.click()

    WebDriverWait(driver, 30).until(lambda d: "signin" not in d.current_url.lower())
    log.info("Logged in to Tableau Server")


def _switch_to_viz_frame(driver: webdriver.Chrome):
    """Switch into the Tableau viz iframe if present, else stay in main frame."""
    driver.switch_to.default_content()
    for sel in [
        "iframe#tableau_frame",
        "iframe[id*='tableau']",
        "iframe[class*='tableau']",
        "iframe[src*='views']",
    ]:
        try:
            iframe = WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            driver.switch_to.frame(iframe)
            log.info("Switched to viz iframe: %s", sel)
            return
        except TimeoutException:
            continue
    log.info("No viz iframe found — operating in main frame")


def _debug_dump(driver: webdriver.Chrome, label: str):
    """Save a screenshot and log all inputs found in current frame."""
    try:
        path = str(Path(CATEGORY_DIR) / f"debug_{label}.png")
        driver.save_screenshot(path)
        log.info("Screenshot saved: %s", path)
    except Exception as e:
        log.info("Screenshot failed: %s", e)

    inputs = driver.find_elements(By.TAG_NAME, "input")
    log.info("Inputs in current frame (%s): %d", label, len(inputs))
    for i, inp in enumerate(inputs):
        try:
            parent_html = driver.execute_script(
                "return arguments[0].parentElement ? arguments[0].parentElement.outerHTML : '';", inp
            )[:300]
            log.info(
                "  input[%d] type=%s class=%s value=%r displayed=%s rect=%s",
                i, inp.get_attribute("type"), inp.get_attribute("class"),
                inp.get_attribute("value"), inp.is_displayed(), inp.rect,
            )
            log.info("      parentHTML[%d]: %s", i, parent_html)
        except Exception as e:
            log.info("  input[%d] inspect failed: %s", i, e)


def _find_access_key_input(driver: webdriver.Chrome):
    """Search current frame (and one level of nested iframes) for the Access Key text input."""
    strategies = [
        (By.CSS_SELECTOR, "input.QueryBox"),
        (By.XPATH, "//input[contains(@class,'QueryBox')]"),
        (By.XPATH, "//div[contains(@class,'tab-parameterControl')]//input[@type='text']"),
        (By.CSS_SELECTOR, "input.tab-parameterControl-text"),
        (By.XPATH, "//input[@type='text' and not(@disabled) and not(@readonly)]"),
        (By.CSS_SELECTOR, "input[type='text']"),
    ]
    for by, sel in strategies:
        try:
            el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
            log.info("Found Access Key input via: %s", sel)
            return el
        except TimeoutException:
            continue

    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    log.info("Searching %d nested iframes for Access Key input", len(iframes))
    for i, frame in enumerate(iframes):
        try:
            driver.switch_to.frame(frame)
            for by, sel in strategies:
                try:
                    el = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, sel)))
                    log.info("Found Access Key input in nested iframe[%d] via: %s", i, sel)
                    return el
                except TimeoutException:
                    continue
            driver.switch_to.parent_frame()
        except Exception as exc:
            log.info("Could not search iframe[%d]: %s", i, exc)
            try:
                driver.switch_to.parent_frame()
            except Exception:
                pass

    return None


def _enter_access_key(driver: webdriver.Chrome):
    """Type the access key into the Access Key text input on GP Overview."""
    if not TABLEAU_HEADLESS:
        _debug_dump(driver, "before_access_key")
    key_input = _find_access_key_input(driver)
    if key_input is None:
        raise RuntimeError("Could not find Access Key input field")

    key_input.clear()
    key_input.send_keys(TABLEAU_ACCESS_KEY)
    key_input.send_keys(Keys.RETURN)
    _wait_for_tableau(driver, extra=2)
    log.info("Entered access key — data loaded")


def _type_into_date_input(driver: webdriver.Chrome, inp, date_str: str):
    """Interact with a (possibly hidden) Tableau date input. JS-clicks it, then tries
    send_keys; if that fails (element not interactable), falls back to JS value injection
    plus synthetic change/keydown events so the Dojo widget picks up the new value."""
    driver.execute_script("arguments[0].click();", inp)
    time.sleep(0.3)
    try:
        inp.send_keys(Keys.CONTROL, "a")
        inp.send_keys(Keys.COMMAND, "a")
        inp.send_keys(Keys.DELETE)
        inp.send_keys(date_str)
        inp.send_keys(Keys.RETURN)
    except Exception:
        # Hidden / non-interactable — inject value via JS and fire events
        driver.execute_script(
            "var el=arguments[0], v=arguments[1];"
            "el.value=v;"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));"
            "el.dispatchEvent(new KeyboardEvent('keydown',{keyCode:13,bubbles:true}));",
            inp, date_str,
        )
        log.info("Set date via JS injection: %s", date_str)


def _set_gp_date_range(driver: webdriver.Chrome, d) -> bool:
    """Set the GP Overview Date filter (from and to) to a single day d.

    The date inputs are hidden inside QFReadout divs. Clicking each div triggers
    Tableau/Dojo's showLowerInput / showUpperInput handler. We JS-click the div,
    then interact with the child input (via send_keys or JS fallback).
    Date format used by the Tableau widget: dd/MM/yyyy (e.g. 07/06/2026).

    Returns True if the from-date was successfully set.
    """
    date_str = d.strftime("%d/%m/%Y")
    log.info("Setting GP Overview date range to %s", date_str)

    def _set_bound(div_css: str, label: str) -> bool:
        try:
            div = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, div_css))
            )
            driver.execute_script("arguments[0].click();", div)
            time.sleep(0.6)
            inp = div.find_element(By.CSS_SELECTOR, "input")
            _type_into_date_input(driver, inp, date_str)
            log.info("Set %s to %s", label, date_str)
            return True
        except Exception as exc:
            log.warning("Failed to set %s: %s", label, exc)
            return False

    try:
        from_ok = _set_bound(".QFLowerBound", "from-date")
        time.sleep(0.5)
        _set_bound(".QFUpperBound", "to-date")
        time.sleep(0.5)

        _wait_for_tableau(driver, extra=1.5)
        log.info("GP Overview date filter set to %s", date_str)
        return from_ok

    except Exception as exc:
        log.warning("Failed to set GP Overview date range: %s", exc)
        _debug_dump(driver, "gp_date_error")
        return False


def _click_category_tab(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, _WAIT)
    for by, sel in [
        (By.XPATH, "//span[normalize-space()='Category Performance']"),
        (By.XPATH, "//*[contains(@class,'tab') and normalize-space()='Category Performance']"),
        (By.XPATH, "//*[normalize-space()='Category Performance']"),
    ]:
        try:
            tab = wait.until(EC.element_to_be_clickable((by, sel)))
            tab.click()
            log.info("Clicked Category Performance tab")
            _wait_for_tableau(driver, extra=2)
            return
        except TimeoutException:
            continue
    raise RuntimeError("Could not find the Category Performance tab")


def _click_download_button(driver: webdriver.Chrome):
    """Click the Tableau Download toolbar button.

    Cycles through candidate selectors every 4 s until one is clickable or the
    45 s deadline is reached. Short per-selector timeouts prevent a hidden element
    that matches early in DOM order from blocking the whole wait.
    """
    _SELECTORS = [
        (By.CSS_SELECTOR, "[data-tb-test-id='DownloadButton-Button']"),
        (By.CSS_SELECTOR, "[data-tb-test-id*='ownload']"),
        (By.CSS_SELECTOR, "[data-tb-test-id*='Download']"),
        (By.CSS_SELECTOR, "button[title='Download']"),
        (By.CSS_SELECTOR, "button[aria-label='Download']"),
        (By.CSS_SELECTOR, "button[title='下載']"),
        (By.CSS_SELECTOR, "button[aria-label='下載']"),
        (By.XPATH, "//button[contains(@aria-label,'下載') or contains(@title,'下載')]"),
        (By.CSS_SELECTOR, ".tab-toolbar-btn-download"),
    ]
    deadline = time.time() + 180
    while time.time() < deadline:
        for by, sel in _SELECTORS:
            try:
                btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, sel)))
                btn.click()
                log.info("Clicked Download button via: %s", sel)
                time.sleep(0.8)
                return
            except TimeoutException:
                continue

    # No selector matched — dump all buttons so we can identify the right one
    if not TABLEAU_HEADLESS:
        try:
            driver.save_screenshot(os.path.join(DAILY_DIR, "debug_no_download_btn.png"))
            log.info("Screenshot saved to %s/debug_no_download_btn.png", DAILY_DIR)
        except Exception:
            pass
    buttons = driver.find_elements(By.TAG_NAME, "button")
    log.warning("=== ALL BUTTONS (main frame) count=%d ===", len(buttons))
    for i, b in enumerate(buttons):
        try:
            log.warning("  [%d] text=%r title=%r aria-label=%r data-tb-test-id=%r displayed=%s",
                i, b.text[:40], b.get_attribute("title"), b.get_attribute("aria-label"),
                b.get_attribute("data-tb-test-id"), b.is_displayed())
        except Exception:
            pass
    raise RuntimeError("Could not find a clickable Download button within 45 s")


def _download_crosstab(driver: webdriver.Chrome):
    _click_download_button(driver)

    ct_btn = WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.XPATH,
        "//*[@data-tb-test-id='DownloadCrosstab-Button' or "
        "normalize-space()='交叉資料表' or normalize-space()='Crosstab']"
    )))
    _js_click(driver, ct_btn)
    log.info("Clicked Crosstab")
    time.sleep(1.5)

    dl_btn = WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.XPATH,
        "//*[@data-tb-test-id='export-crosstab-export-Button' or "
        "(local-name()='button' and "
        "(normalize-space()='下載' or normalize-space()='Download'))]"
    )))
    _js_click(driver, dl_btn)
    log.info("Clicked 下載 in crosstab dialog")


def _wait_for_download(since: float, date_str: str) -> str | None:
    """Wait for a new xlsx to finish downloading, then rename it to include the data date."""
    deadline = time.time() + _DOWNLOAD_TIMEOUT
    while time.time() < deadline:
        partial = [f for f in os.listdir(CATEGORY_DIR) if f.endswith(".crdownload")]
        candidates = [
            os.path.join(CATEGORY_DIR, f)
            for f in os.listdir(CATEGORY_DIR)
            if f.endswith(".xlsx") and not f.startswith(".")
        ]
        recent = [p for p in candidates if os.path.getmtime(p) >= since - 2]
        if recent and not partial:
            newest = max(recent, key=os.path.getmtime)
            size1 = os.path.getsize(newest)
            time.sleep(2)
            size2 = os.path.getsize(newest)
            if size1 == size2 > 0:
                target = os.path.join(CATEGORY_DIR, f"category_performance_{date_str}.xlsx")
                os.replace(newest, target)
                log.info("Downloaded and renamed to: %s (%d bytes)", target, size2)
                return target
        time.sleep(2)
    return None


def download_category_performance() -> str | None:
    """Download the Category Performance crosstab xlsx for yesterday. Returns path or None."""
    if not all([TABLEAU_SERVER, TABLEAU_USERNAME, TABLEAU_PASSWORD, TABLEAU_ACCESS_KEY]):
        log.warning("Tableau credentials not configured — skipping download")
        return None

    os.makedirs(CATEGORY_DIR, exist_ok=True)
    d = (datetime.now() - timedelta(days=1)).date()
    file_date = d.strftime("%Y-%m-%d")
    start = time.time()
    driver = None
    try:
        gp_url = f"{TABLEAU_SERVER}/views/{_WORKBOOK}/{_GP_OVERVIEW}"
        driver = _make_driver()
        _login(driver, target_url=gp_url)   # navigate directly — skips extra /#/signin load
        _wait_for_tableau(driver, extra=2)
        log.info("Loaded GP Overview")

        _switch_to_viz_frame(driver)
        _enter_access_key(driver)

        if not _set_gp_date_range(driver, d):
            log.error("Could not set date filter to %s — aborting to avoid wrong-day data", file_date)
            return None

        _click_category_tab(driver)
        _download_crosstab(driver)

        path = _wait_for_download(since=start, date_str=file_date)
        if not path:
            log.error("Download timed out after %ds", _DOWNLOAD_TIMEOUT)
        return path

    except Exception as exc:
        log.error("Tableau download failed: %s", exc, exc_info=True)
        return None
    finally:
        if driver:
            driver.quit()


def backfill_category_performance(dates: list) -> dict:
    """Download Category Performance for each date in `dates` (list of date objects).

    Logs in once, enters the access key once (it persists for the Tableau session),
    then for each date reloads GP Overview, sets the date range, switches to Category
    Performance, and downloads. Returns {iso_date: path_or_None}.
    """
    results: dict = {}
    if not all([TABLEAU_SERVER, TABLEAU_USERNAME, TABLEAU_PASSWORD, TABLEAU_ACCESS_KEY]):
        log.warning("Tableau credentials not configured — skipping backfill")
        return results

    os.makedirs(CATEGORY_DIR, exist_ok=True)
    driver = None
    try:
        gp_url = f"{TABLEAU_SERVER}/views/{_WORKBOOK}/{_GP_OVERVIEW}"
        driver = _make_driver()
        _login(driver, target_url=gp_url)   # navigate directly — skips extra /#/signin load

        # Enter the access key once — it persists for the duration of this session
        _wait_for_tableau(driver, extra=2)
        _switch_to_viz_frame(driver)
        _enter_access_key(driver)
        log.info("Access key entered — starting per-date backfill loop (%d dates)", len(dates))

        for d in dates:
            iso = d.strftime("%Y-%m-%d")
            log.info("--- Backfilling %s ---", iso)

            driver.get(f"{TABLEAU_SERVER}/views/{_WORKBOOK}/{_GP_OVERVIEW}")
            _wait_for_tableau(driver, extra=2)
            _switch_to_viz_frame(driver)

            if not _set_gp_date_range(driver, d):
                log.error("Skipping %s — could not set date filter", iso)
                results[iso] = None
                continue

            _click_category_tab(driver)

            start = time.time()
            try:
                _download_crosstab(driver)
                path = _wait_for_download(since=start, date_str=iso)
                results[iso] = path
                if path:
                    log.info("Stored: %s", path)
                else:
                    log.error("Download timed out for %s", iso)
            except Exception as exc:
                log.error("Download failed for %s: %s", iso, exc)
                results[iso] = None

            time.sleep(2)

        return results

    except Exception as exc:
        log.error("Backfill session failed: %s", exc, exc_info=True)
        return results
    finally:
        if driver:
            driver.quit()


# ── Category GMV (Tableau REST API, PAT2) ─────────────────────────────────────

def _pat2_signin(base: str) -> tuple[str, str]:
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


def _find_view_id(base: str, headers: dict, site_id: str,
                  workbook_content_url: str, view_url_name: str) -> str:
    resp = requests.get(
        f"{base}/sites/{site_id}/workbooks",
        headers=headers,
        params={"filter": f"contentUrl:eq:{workbook_content_url}"},
        timeout=30, verify=False,
    )
    resp.raise_for_status()
    wbs = resp.json().get("workbooks", {}).get("workbook", [])
    if not wbs:
        raise RuntimeError(f"Workbook '{workbook_content_url}' not found (does PAT2 have access?)")
    wb_id = wbs[0]["id"]

    resp = requests.get(
        f"{base}/sites/{site_id}/workbooks/{wb_id}/views",
        headers=headers, timeout=30, verify=False,
    )
    resp.raise_for_status()
    views = resp.json().get("views", {}).get("view", [])
    view = next((v for v in views if v.get("viewUrlName") == view_url_name), None)
    if not view:
        raise RuntimeError(f"View '{view_url_name}' not found in workbook '{workbook_content_url}'")
    return view["id"]


def _parse_category_gmv_xlsx(content: bytes) -> list[dict]:
    """Parse the crosstab xlsx from MonthlySalesbystore/bycat into category rows.

    Layout (14 cols): code/name pairs for Sub Cat 1-4 (cols 1-8), then
    GMV / Cust # / Parent Order # for the month group (cols 9-11) and the
    Total group (cols 12-14). Parent cells are merged (openpyxl → None), so we
    forward-fill. We keep only leaf rows: skip subtotal rows ('Total' in any
    code column) and the final 'Grand Total' row. Validated to reproduce the
    manual download's grand total exactly.

    Maps Sub Cat 1-4 Chinese names → main_cat, sub_cat1, sub_cat2, sub_cat3.
    gp / gp_pct are 0 (not available in this view).
    """
    import io
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.worksheets[0]

    code_cols = [1, 3, 5, 7]
    name_cols = [2, 4, 6, 8]
    last = {c: "" for c in code_cols + name_cols}
    rows: list[dict] = []

    for r in range(3, ws.max_row + 1):   # rows 1-2 are headers
        c1 = ws.cell(r, 1).value
        if c1 == "Grand Total":
            continue
        if any(ws.cell(r, c).value == "Total" for c in code_cols):
            continue   # subtotal row

        names = []
        for cc, nc in zip(code_cols, name_cols):
            cv = ws.cell(r, cc).value
            nv = ws.cell(r, nc).value
            if cv is None:
                cv = last[cc]
            else:
                last[cc] = cv
            if nv is None:
                nv = last[nc]
            else:
                last[nc] = nv
            names.append((nv or "").strip())

        gmv = ws.cell(r, 9).value
        gmv = float(gmv) if isinstance(gmv, (int, float)) else 0.0

        db_keys = ["main_cat", "sub_cat1", "sub_cat2", "sub_cat3", "sub_cat4"]
        resolved = dict(zip(db_keys, names + [""]))

        leaf, level = "", 0
        for i, k in enumerate(db_keys):
            if resolved[k]:
                leaf = resolved[k]
                level = i + 1

        rows.append({**resolved, "leaf_cat": leaf, "level": level,
                     "gmv": gmv, "gp": 0.0, "gp_pct": 0.0})

    # Deduplicate: different Sub Cat codes can share the same Chinese name.
    # Aggregate by category name key, summing GMV.
    seen: dict = {}
    for r in rows:
        key = (r["main_cat"], r["sub_cat1"], r["sub_cat2"], r["sub_cat3"], r["sub_cat4"])
        if key in seen:
            seen[key]["gmv"] += r["gmv"]
        else:
            seen[key] = r
    return list(seen.values())


def _download_category_xlsx(base: str, headers: dict, site_id: str,
                            view_id: str, date_str: str) -> bytes:
    """GET the crosstab Excel for a single day from the bycat view."""
    resp = requests.get(
        f"{base}/sites/{site_id}/views/{view_id}/crosstab/excel",
        headers=headers,
        params={"vf_Order Date (day)": date_str},
        timeout=180, verify=False,
    )
    resp.raise_for_status()
    return resp.content


def _save_category_xlsx(content: bytes, date_str: str) -> str:
    """Write the downloaded crosstab xlsx to CATEGORY_DIR for auditing."""
    os.makedirs(CATEGORY_DIR, exist_ok=True)
    path = os.path.join(CATEGORY_DIR, f"category_performance_{date_str}.xlsx")
    with open(path, "wb") as f:
        f.write(content)
    return path


def download_category_gmv_rest(target_date=None) -> list[dict]:
    """Download GMV by category from MonthlySalesbystore/bycat via REST API (PAT2).

    Returns list of dicts matching upsert_category_rows schema.
    Falls back silently to [] if PAT2 not configured or request fails.
    """
    if not all([TABLEAU_SERVER, TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET]):
        log.warning("TABLEAU_PAT2 credentials not configured — skipping REST category download")
        return []

    if target_date is None:
        target_date = (datetime.now() - timedelta(days=1)).date()
    date_str = target_date.strftime("%Y-%m-%d")
    base = f"{TABLEAU_SERVER}/api/{_API_VERSION}"

    try:
        token, site_id = _pat2_signin(base)
        headers = {"x-tableau-auth": token, "accept": "application/json"}
        log.info("PAT2 signed in for category GMV download")

        view_id = _find_view_id(base, headers, site_id, _CAT_GMV_WORKBOOK, _CAT_GMV_VIEW)
        content = _download_category_xlsx(base, headers, site_id, view_id, date_str)
        saved = _save_category_xlsx(content, date_str)
        rows = _parse_category_gmv_xlsx(content)
        total = sum(r["gmv"] for r in rows)
        log.info("Category GMV REST: %d rows, total GMV %s for %s (saved %s)",
                 len(rows), f"{total:,.0f}", date_str, saved)

        requests.post(f"{base}/auth/signout", headers=headers, timeout=10, verify=False)
        return rows

    except Exception as exc:
        log.error("Category GMV REST download failed for %s: %s", date_str, exc, exc_info=True)
        return []


def backfill_category_gmv_rest(dates: list) -> dict:
    """Download category GMV via REST API for each date. Returns {iso_date: row_list}."""
    if not all([TABLEAU_SERVER, TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET]):
        log.warning("TABLEAU_PAT2 credentials not configured — skipping REST backfill")
        return {}

    base = f"{TABLEAU_SERVER}/api/{_API_VERSION}"
    results: dict = {}

    try:
        token, site_id = _pat2_signin(base)
        headers = {"x-tableau-auth": token, "accept": "application/json"}
        log.info("PAT2 signed in — starting REST category GMV backfill (%d dates)", len(dates))

        view_id = _find_view_id(base, headers, site_id, _CAT_GMV_WORKBOOK, _CAT_GMV_VIEW)

        for d in dates:
            iso = d.strftime("%Y-%m-%d")
            try:
                content = _download_category_xlsx(base, headers, site_id, view_id, iso)
                _save_category_xlsx(content, iso)
                rows = _parse_category_gmv_xlsx(content)
                total = sum(r["gmv"] for r in rows)
                log.info("  %s: %d rows, total GMV %s", iso, len(rows), f"{total:,.0f}")
                results[iso] = rows
            except Exception as exc:
                log.error("  %s failed: %s", iso, exc)
                results[iso] = None

        requests.post(f"{base}/auth/signout", headers=headers, timeout=10, verify=False)

    except Exception as exc:
        log.error("REST backfill session failed: %s", exc, exc_info=True)

    return results


# ── Store Performance by RM by Category (Tableau REST API, PAT2) ─────────────

def _parse_store_gmv_xlsx(content: bytes) -> list[dict]:
    """Parse the crosstab xlsx from MonthlySalesbystore/bystorebymaincat.

    Columns: Team_Head(0), RM_Code(1), RM_Name(2), Store_Code(3), Store_Name(4),
    main_cat(5), Consignment_SKU(6), SKU#(7), 3PL_SKU#(8), GMV(9), Cust#(10), Orders(11).
    Merged cells in cols 0–4 → forward-fill. Skip Grand Total row. Dedup by
    (store_code, main_cat) summing GMV/customers/orders.
    """
    import io
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.worksheets[0]

    last: dict = {c: "" for c in range(5)}
    rows: list[dict] = []

    for r in range(2, ws.max_row + 1):   # row 1 is header
        col0 = ws.cell(r, 1).value
        if col0 == "Grand Total":
            continue

        vals = []
        for c in range(5):
            v = ws.cell(r, c + 1).value
            if v is None:
                v = last[c]
            else:
                last[c] = v
            vals.append((v or "").strip() if isinstance(v, str) else (v or ""))

        team_head, rm_code, rm_name, store_code, store_name = vals
        main_cat = (ws.cell(r, 6).value or "").strip()
        gmv      = ws.cell(r, 10).value
        cust     = ws.cell(r, 11).value
        orders   = ws.cell(r, 12).value

        gmv    = float(gmv)    if isinstance(gmv,    (int, float)) else 0.0
        cust   = int(cust)     if isinstance(cust,   (int, float)) else 0
        orders = int(orders)   if isinstance(orders, (int, float)) else 0

        rows.append({
            "rm_code":    str(rm_code).strip(),
            "rm_name":    str(rm_name).strip(),
            "store_code": str(store_code).strip(),
            "store_name": str(store_name).strip(),
            "main_cat":   main_cat,
            "gmv":        gmv,
            "customers":  cust,
            "orders":     orders,
        })

    # Deduplicate: same (store_code, main_cat) may appear with different internal codes.
    seen: dict = {}
    for r in rows:
        key = (r["store_code"], r["main_cat"])
        if key in seen:
            seen[key]["gmv"]       += r["gmv"]
            seen[key]["customers"] += r["customers"]
            seen[key]["orders"]    += r["orders"]
        else:
            seen[key] = r
    return list(seen.values())


def _save_store_xlsx(content: bytes, date_str: str) -> str:
    """Write the downloaded store crosstab xlsx to STORE_DIR for auditing."""
    os.makedirs(STORE_DIR, exist_ok=True)
    path = os.path.join(STORE_DIR, f"store_performance_{date_str}.xlsx")
    with open(path, "wb") as f:
        f.write(content)
    return path


def download_store_gmv_rest(target_date=None) -> list[dict]:
    """Download GMV by store × main-category from MonthlySalesbystore/bystorebymaincat.

    Returns list of dicts matching upsert_store_rows schema.
    Falls back silently to [] if PAT2 not configured or request fails.
    """
    if not all([TABLEAU_SERVER, TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET]):
        log.warning("TABLEAU_PAT2 credentials not configured — skipping REST store download")
        return []

    if target_date is None:
        target_date = (datetime.now() - timedelta(days=1)).date()
    date_str = target_date.strftime("%Y-%m-%d")
    base = f"{TABLEAU_SERVER}/api/{_API_VERSION}"

    try:
        token, site_id = _pat2_signin(base)
        headers = {"x-tableau-auth": token, "accept": "application/json"}

        view_id = _find_view_id(base, headers, site_id, _CAT_GMV_WORKBOOK, _STORE_GMV_VIEW)
        resp = requests.get(
            f"{base}/sites/{site_id}/views/{view_id}/crosstab/excel",
            headers=headers,
            params={"vf_Order Date (day)": date_str},
            timeout=180, verify=False,
        )
        resp.raise_for_status()
        content = resp.content
        saved = _save_store_xlsx(content, date_str)
        rows = _parse_store_gmv_xlsx(content)
        total = sum(r["gmv"] for r in rows)
        log.info("Store GMV REST: %d rows, total GMV %s for %s (saved %s)",
                 len(rows), f"{total:,.0f}", date_str, saved)

        requests.post(f"{base}/auth/signout", headers=headers, timeout=10, verify=False)
        return rows

    except Exception as exc:
        log.error("Store GMV REST download failed for %s: %s", date_str, exc, exc_info=True)
        return []


def backfill_store_gmv_rest(dates: list) -> dict:
    """Download store × category GMV via REST API for each date. Returns {iso_date: rows_or_None}."""
    if not all([TABLEAU_SERVER, TABLEAU_PAT2_NAME, TABLEAU_PAT2_SECRET]):
        log.warning("TABLEAU_PAT2 credentials not configured — skipping REST store backfill")
        return {}

    base = f"{TABLEAU_SERVER}/api/{_API_VERSION}"
    results: dict = {}

    try:
        token, site_id = _pat2_signin(base)
        headers = {"x-tableau-auth": token, "accept": "application/json"}
        log.info("PAT2 signed in — starting REST store GMV backfill (%d dates)", len(dates))

        view_id = _find_view_id(base, headers, site_id, _CAT_GMV_WORKBOOK, _STORE_GMV_VIEW)

        for d in dates:
            iso = d.strftime("%Y-%m-%d")
            try:
                resp = requests.get(
                    f"{base}/sites/{site_id}/views/{view_id}/crosstab/excel",
                    headers=headers,
                    params={"vf_Order Date (day)": iso},
                    timeout=180, verify=False,
                )
                resp.raise_for_status()
                content = resp.content
                _save_store_xlsx(content, iso)
                rows = _parse_store_gmv_xlsx(content)
                total = sum(r["gmv"] for r in rows)
                log.info("  %s: %d rows, total GMV %s", iso, len(rows), f"{total:,.0f}")
                results[iso] = rows
            except Exception as exc:
                log.error("  %s failed: %s", iso, exc)
                results[iso] = None

        requests.post(f"{base}/auth/signout", headers=headers, timeout=10, verify=False)

    except Exception as exc:
        log.error("REST store backfill session failed: %s", exc, exc_info=True)

    return results


# ── Daily Sales Update (Tableau REST API) ─────────────────────────────────────

_API_VERSION = "3.15"


def download_daily_sales_update() -> str | None:
    """Download the Daily Sales Update PDF via Tableau REST API using a PAT."""
    if not all([TABLEAU_SERVER, TABLEAU_PAT_NAME, TABLEAU_PAT_SECRET]):
        log.warning("Tableau PAT credentials not configured — skipping download")
        return None

    os.makedirs(DAILY_DIR, exist_ok=True)
    file_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    base = f"{TABLEAU_SERVER}/api/{_API_VERSION}"

    try:
        # 1. Sign in
        resp = requests.post(
            f"{base}/auth/signin",
            json={"credentials": {
                "personalAccessTokenName": TABLEAU_PAT_NAME,
                "personalAccessTokenSecret": TABLEAU_PAT_SECRET,
                "site": {"contentUrl": ""},
            }},
            headers={"content-type": "application/json", "accept": "application/json"},
            timeout=30,
            verify=False,
        )
        if not resp.ok:
            log.error("Sign-in failed: %s %s", resp.status_code, resp.text[:500])
            return None
        creds = resp.json()["credentials"]
        token = creds["token"]
        site_id = creds["site"]["id"]
        headers = {"x-tableau-auth": token, "accept": "application/json"}
        log.info("Signed in to Tableau REST API (site_id=%s)", site_id)

        # 2. Find the workbook by its content URL, then get the exact view from it
        resp = requests.get(
            f"{base}/sites/{site_id}/workbooks",
            headers=headers,
            params={"filter": f"contentUrl:eq:{_DAILY_WORKBOOK}"},
            timeout=30,
            verify=False,
        )
        resp.raise_for_status()
        workbooks = resp.json().get("workbooks", {}).get("workbook", [])
        if not workbooks:
            log.error("No workbook found with contentUrl=%s", _DAILY_WORKBOOK)
            return None
        workbook_id = workbooks[0]["id"]
        log.info("Found workbook: id=%s name=%s", workbook_id, workbooks[0].get("name"))

        resp = requests.get(
            f"{base}/sites/{site_id}/workbooks/{workbook_id}/views",
            headers=headers,
            timeout=30,
            verify=False,
        )
        resp.raise_for_status()
        views = resp.json().get("views", {}).get("view", [])
        view = next((v for v in views if v.get("viewUrlName") == _DAILY_SHEET), None)
        if not view:
            log.error("No view '%s' in workbook '%s'. Available: %s",
                      _DAILY_SHEET, _DAILY_WORKBOOK, [v.get("viewUrlName") for v in views])
            return None
        view_id = view["id"]
        log.info("Found view: id=%s name=%s", view_id, view.get("name"))

        # 3. Download PDF
        resp = requests.get(
            f"{base}/sites/{site_id}/views/{view_id}/pdf",
            headers=headers,
            timeout=120,
            stream=True,
            verify=False,
        )
        resp.raise_for_status()

        path = os.path.join(DAILY_DIR, f"daily_sales_{file_date}.pdf")
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        size = os.path.getsize(path)
        log.info("Downloaded PDF via REST API: %s (%d bytes)", path, size)

        # 4. Sign out
        requests.post(f"{base}/auth/signout", headers=headers, timeout=10, verify=False)

        return path

    except Exception as exc:
        log.error("Daily Sales Update REST API download failed: %s", exc, exc_info=True)
        return None
