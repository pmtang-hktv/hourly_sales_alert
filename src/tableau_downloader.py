"""Automated download of Category Performance crosstab from Tableau Server.

Flow (matches the manual steps):
  1. Log in to Tableau Server
  2. On GP Overview: enter the Access Key, set the date range (from=to=target date)
  3. Click the "Category Performance" tab (date carries over as a workbook parameter)
  4. Download > Crosstab (交叉資料表) > Excel > 下載
  5. Wait for the xlsx, rename it to category_performance_YYYY-MM-DD.xlsx
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from src.config import CATEGORY_DIR, TABLEAU_ACCESS_KEY, TABLEAU_HEADLESS, TABLEAU_PASSWORD, TABLEAU_SERVER, TABLEAU_USERNAME

log = logging.getLogger(__name__)

_WAIT = 30
_DOWNLOAD_TIMEOUT = 120
_WORKBOOK = "RMDashboard-GPReport"
_GP_OVERVIEW = "GPOverview"

# GP Overview date fields use yyyy/m/d (not zero-padded)
_DATE_YMD_RE = re.compile(r"^\d{4}/\d+/\d+$")


def _make_driver() -> webdriver.Chrome:
    abs_dir = str(Path(CATEGORY_DIR).resolve())
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


def _login(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, _WAIT)
    driver.get(f"{TABLEAU_SERVER}/#/signin")

    for sel in ["input#username", "input[name='username']", "input[autocomplete='username']", "input[type='text']"]:
        try:
            f = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            f.clear()
            f.send_keys(TABLEAU_USERNAME)
            break
        except TimeoutException:
            continue

    for sel in ["input#password", "input[name='password']", "input[type='password']"]:
        try:
            f = driver.find_element(By.CSS_SELECTOR, sel)
            f.clear()
            f.send_keys(TABLEAU_PASSWORD)
            break
        except Exception:
            continue

    for sel in ["button[type='submit']", "button.signin-btn", "#signin-btn", "input[type='submit']"]:
        try:
            driver.find_element(By.CSS_SELECTOR, sel).click()
            break
        except Exception:
            continue

    wait.until(lambda d: "signin" not in d.current_url.lower())
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


def _download_crosstab(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, _WAIT)

    # 1. Click the Download toolbar button
    dl_btn = None
    for by, sel in [
        (By.CSS_SELECTOR, "[data-tb-test-id='DownloadButton-Button']"),
        (By.CSS_SELECTOR, "button[title='Download']"),
        (By.CSS_SELECTOR, "button[aria-label='Download']"),
        (By.XPATH, "//button[contains(@aria-label,'下載') or contains(@title,'下載')]"),
        (By.CSS_SELECTOR, ".tab-toolbar-btn-download"),
        (By.CSS_SELECTOR, "[data-tb-test-id*='ownload']"),
    ]:
        try:
            dl_btn = wait.until(EC.element_to_be_clickable((by, sel)))
            log.info("Found Download button via: %s", sel)
            break
        except TimeoutException:
            continue
    if dl_btn is None:
        raise RuntimeError("Could not find Download button in Tableau toolbar")
    dl_btn.click()
    time.sleep(1)

    # 2. Click 交叉資料表 (Crosstab) in the dropdown
    for by, sel in [
        (By.CSS_SELECTOR, "[data-tb-test-id='DownloadCrosstab-Button']"),
        (By.XPATH, "//*[normalize-space()='交叉資料表']"),
        (By.XPATH, "//*[normalize-space()='Crosstab']"),
        (By.XPATH, "//*[contains(text(),'交叉資料表')]"),
    ]:
        try:
            el = wait.until(EC.element_to_be_clickable((by, sel)))
            el.click()
            log.info("Clicked Crosstab (交叉資料表)")
            break
        except TimeoutException:
            continue
    time.sleep(1.5)

    # 3. Excel is the default format — click 下載
    for by, sel in [
        (By.CSS_SELECTOR, "[data-tb-test-id='export-crosstab-export-Button']"),
        (By.XPATH, "//button[normalize-space()='下載']"),
        (By.XPATH, "//button[normalize-space()='Download']"),
        (By.XPATH, "//*[@role='button' and (normalize-space()='下載' or normalize-space()='Download')]"),
    ]:
        try:
            btn = wait.until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            log.info("Clicked 下載 in crosstab dialog")
            return
        except TimeoutException:
            continue
    raise RuntimeError("Could not find the 下載/Download button in the crosstab dialog")


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
        driver = _make_driver()
        _login(driver)

        driver.get(f"{TABLEAU_SERVER}/views/{_WORKBOOK}/{_GP_OVERVIEW}")
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
        driver = _make_driver()
        _login(driver)

        # Enter the access key once — it persists for the duration of this session
        driver.get(f"{TABLEAU_SERVER}/views/{_WORKBOOK}/{_GP_OVERVIEW}")
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
