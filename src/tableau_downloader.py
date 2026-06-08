"""Automated download of Category Performance crosstab from Tableau Server.

Flow (matches the manual steps):
  1. Log in
  2. On GP Overview, enter the Access Key and press Enter (parameter is workbook-wide)
  3. Click the "Category Performance" tab
  4. Set the date range to yesterday (from & to)
  5. Download > Crosstab (交叉資料表) > Excel > 下載
  6. Wait for the xlsx to finish downloading into CATEGORY_DIR
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
_DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")


def _make_driver() -> webdriver.Chrome:
    abs_dir = str(Path(CATEGORY_DIR).resolve())
    opts = Options()
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


def _wait_for_tableau(driver: webdriver.Chrome, extra: float = 3.0):
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


def _enter_access_key(driver: webdriver.Chrome):
    """Type the access key into the only plain text input in the filter bar (GP Overview)."""
    wait = WebDriverWait(driver, _WAIT)
    strategies = [
        (By.XPATH, "//div[contains(@class,'tab-parameterControl')]//input[@type='text']"),
        (By.CSS_SELECTOR, "input.tab-parameterControl-text"),
        (By.XPATH, "//input[@type='text' and not(@disabled) and not(@readonly)]"),
        (By.CSS_SELECTOR, "input[type='text']"),
    ]
    key_input = None
    for by, sel in strategies:
        try:
            key_input = wait.until(EC.element_to_be_clickable((by, sel)))
            log.info("Found Access Key input via: %s", sel)
            break
        except TimeoutException:
            continue
    if key_input is None:
        raise RuntimeError("Could not find Access Key input field")

    key_input.clear()
    key_input.send_keys(TABLEAU_ACCESS_KEY)
    key_input.send_keys(Keys.RETURN)
    _wait_for_tableau(driver, extra=4)
    log.info("Entered access key — data loaded")


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
            _wait_for_tableau(driver, extra=4)
            return
        except TimeoutException:
            continue
    raise RuntimeError("Could not find the Category Performance tab")


def _set_date_range(driver: webdriver.Chrome, date_str: str):
    """Set both the from and to date fields of the range-date filter to date_str.

    Tableau's range date filter renders the from/to dates as editable text inputs
    that currently hold values like '2026/6/7'. We locate inputs whose value looks
    like a date and overwrite both. Non-fatal: if the dashboard already defaults to
    yesterday this is a harmless no-op; we log and continue on failure.
    """
    try:
        inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text']")
        date_inputs = []
        for el in inputs:
            try:
                val = (el.get_attribute("value") or "").strip()
                if _DATE_RE.match(val):
                    date_inputs.append(el)
            except Exception:
                continue

        if not date_inputs:
            log.warning("No date-range inputs found — relying on dashboard default (%s)", date_str)
            return

        for el in date_inputs[:2]:  # from, then to
            try:
                el.click()
                el.send_keys(Keys.CONTROL, "a")
                el.send_keys(Keys.COMMAND, "a")  # macOS
                el.send_keys(Keys.DELETE)
                el.send_keys(date_str)
                el.send_keys(Keys.RETURN)
                time.sleep(1.5)
            except Exception as exc:
                log.warning("Failed to set a date input: %s", exc)

        _wait_for_tableau(driver, extra=4)
        log.info("Set date range to %s", date_str)
    except Exception as exc:
        log.warning("Could not set date range (%s) — continuing with default: %s", date_str, exc)


def _download_crosstab(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, _WAIT)

    # 1. Click the Download toolbar button (icon left of 共用/Share)
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

    # 3. In the dialog, Excel is the default format — click 下載 (Download)
    for by, sel in [
        (By.CSS_SELECTOR, "[data-tb-test-id='export-crosstab-export-Button']"),
        (By.XPATH, "//button[normalize-space()='下載']"),
        (By.XPATH, "//button[normalize-space()='Download']"),
        (By.XPATH, "//*[@role='button' and (normalize-space()='下載' or normalize-space()='Download')]"),
    ]:
        try:
            btn = wait.until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            log.info("Clicked 下載 (Download) in crosstab dialog")
            return
        except TimeoutException:
            continue
    raise RuntimeError("Could not find the 下載/Download button in the crosstab dialog")


def _wait_for_download(since: float) -> str | None:
    """Wait for a new xlsx to finish downloading in CATEGORY_DIR."""
    deadline = time.time() + _DOWNLOAD_TIMEOUT
    while time.time() < deadline:
        # An in-progress Chrome download leaves a .crdownload file
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
                log.info("Downloaded: %s (%d bytes)", newest, size2)
                return newest
        time.sleep(2)
    return None


def download_category_performance() -> str | None:
    """Download the Category Performance crosstab xlsx for yesterday. Returns path or None."""
    if not all([TABLEAU_SERVER, TABLEAU_USERNAME, TABLEAU_PASSWORD, TABLEAU_ACCESS_KEY]):
        log.warning("Tableau credentials not configured — skipping download")
        return None

    os.makedirs(CATEGORY_DIR, exist_ok=True)
    d = datetime.now() - timedelta(days=1)
    date_str = f"{d.year}/{d.month}/{d.day}"  # Tableau display format e.g. 2026/6/7
    start = time.time()
    driver = None
    try:
        driver = _make_driver()
        _login(driver)

        driver.get(f"{TABLEAU_SERVER}/views/{_WORKBOOK}/{_GP_OVERVIEW}")
        _wait_for_tableau(driver, extra=3)
        log.info("Loaded GP Overview view")

        _switch_to_viz_frame(driver)
        _enter_access_key(driver)
        _click_category_tab(driver)
        _set_date_range(driver, date_str)
        _download_crosstab(driver)

        path = _wait_for_download(since=start)
        if not path:
            log.error("Download timed out after %ds", _DOWNLOAD_TIMEOUT)
        return path

    except Exception as exc:
        log.error("Tableau download failed: %s", exc, exc_info=True)
        return None
    finally:
        if driver:
            driver.quit()
