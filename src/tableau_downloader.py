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

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from src.config import CATEGORY_DIR, DAILY_DIR, TABLEAU_ACCESS_KEY, TABLEAU_HEADLESS, TABLEAU_PAT_NAME, TABLEAU_PAT_SECRET, TABLEAU_PASSWORD, TABLEAU_SERVER, TABLEAU_USERNAME

log = logging.getLogger(__name__)

_WAIT = 30
_DOWNLOAD_TIMEOUT = 300
_WORKBOOK = "RMDashboard-GPReport"
_GP_OVERVIEW = "GPOverview"
_DAILY_WORKBOOK = "DailySalesUpdate-BQSimplfied"
_DAILY_SHEET = "DailySalesUpdate"

# GP Overview date fields use yyyy/m/d (not zero-padded)
_DATE_YMD_RE = re.compile(r"^\d{4}/\d+/\d+$")


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


def _find_date_inputs(driver: webdriver.Chrome) -> list:
    """Return visible text inputs whose current value is in yyyy/m/d format."""
    all_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input:not([type])")
    return [
        el for el in all_inputs
        if el.is_displayed() and _DATE_YMD_RE.match(el.get_attribute("value") or "")
    ]


def _type_into_date_input(driver: webdriver.Chrome, inp, date_str: str):
    """Click a date input, select-all, replace with date_str, confirm with Enter."""
    driver.execute_script("arguments[0].click();", inp)
    time.sleep(0.3)
    inp.send_keys(Keys.CONTROL, "a")
    inp.send_keys(Keys.COMMAND, "a")   # macOS select-all
    inp.send_keys(Keys.DELETE)
    inp.send_keys(date_str)
    inp.send_keys(Keys.RETURN)


def _set_gp_date_range(driver: webdriver.Chrome, d) -> bool:
    """Set the GP Overview Date filter (from and to) to a single day d.

    The GP Overview date fields accept yyyy/m/d by direct typing. Setting
    from=to=target_date produces a single-day report. If the inputs are not
    immediately visible, we try clicking any visible date-text element first
    (Tableau sometimes shows the date as clickable text that reveals an input).

    Returns True if the from date was successfully set.
    """
    date_str = f"{d.year}/{d.month}/{d.day}"   # e.g. "2026/6/7" (no zero-padding)
    log.info("Setting GP Overview date range to %s", date_str)

    try:
        # Attempt 1: find inputs that already show a date value
        date_inputs = _find_date_inputs(driver)

        if not date_inputs:
            # Attempt 2: click a visible date-text element to reveal the input
            candidates = [
                el for el in driver.find_elements(By.XPATH,
                    "//*[self::span or self::div or self::td][not(self::input)]")
                if el.is_displayed() and _DATE_YMD_RE.match((el.text or "").strip())
            ]
            if candidates:
                log.info("Clicking date text element to reveal input: %r", candidates[0].text)
                driver.execute_script("arguments[0].click();", candidates[0])
                time.sleep(0.5)
                date_inputs = _find_date_inputs(driver)

        if not date_inputs:
            if not TABLEAU_HEADLESS:
                _debug_dump(driver, "gp_date_not_found")
            log.warning("No date inputs found on GP Overview — skipping date set")
            return False

        # Set the "from" date (leftmost / first input)
        _type_into_date_input(driver, date_inputs[0], date_str)
        time.sleep(0.5)

        # Re-fetch inputs after setting "from" (DOM may update)
        date_inputs2 = _find_date_inputs(driver)
        to_candidates = date_inputs2 if len(date_inputs2) >= 2 else date_inputs
        if len(to_candidates) >= 2:
            _type_into_date_input(driver, to_candidates[1], date_str)
            time.sleep(0.5)

        _wait_for_tableau(driver, extra=1.5)
        log.info("GP Overview date set to %s", date_str)
        return True

    except Exception as exc:
        log.warning("Failed to set GP Overview date: %s", exc)
        if not TABLEAU_HEADLESS:
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

        # 2. Find the view by URL name
        resp = requests.get(
            f"{base}/sites/{site_id}/views",
            headers=headers,
            params={"filter": f"viewUrlName:eq:{_DAILY_SHEET}"},
            timeout=30,
            verify=False,
        )
        resp.raise_for_status()
        views = resp.json().get("views", {}).get("view", [])
        if not views:
            log.error("No view found with urlName=%s", _DAILY_SHEET)
            return None
        view_id = views[0]["id"]
        log.info("Found view: id=%s name=%s", view_id, views[0].get("name"))

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
