"""Automated download of Category Performance crosstab from Tableau Server."""
from __future__ import annotations

import logging
import os
import time
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

from src.config import CATEGORY_DIR, TABLEAU_ACCESS_KEY, TABLEAU_PASSWORD, TABLEAU_SERVER, TABLEAU_USERNAME

log = logging.getLogger(__name__)

_WAIT = 30
_DOWNLOAD_TIMEOUT = 120
_WORKBOOK = "RMDashboard-GPReport"
_SHEET = "CategoryPerformance"


def _make_driver() -> webdriver.Chrome:
    abs_dir = str(Path(CATEGORY_DIR).resolve())
    opts = Options()
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
    """Wait for Tableau view to stop showing a loading spinner."""
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            still_loading = driver.execute_script(
                "return !!document.querySelector('.tab-viz-loading, .tabBootProgressBar')"
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

    # Username
    for sel in ["input#username", "input[name='username']", "input[autocomplete='username']", "input[type='text']"]:
        try:
            f = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            f.clear()
            f.send_keys(TABLEAU_USERNAME)
            break
        except TimeoutException:
            continue

    # Password
    for sel in ["input#password", "input[name='password']", "input[type='password']"]:
        try:
            f = driver.find_element(By.CSS_SELECTOR, sel)
            f.clear()
            f.send_keys(TABLEAU_PASSWORD)
            break
        except Exception:
            continue

    # Submit
    for sel in ["button[type='submit']", "button.signin-btn", "#signin-btn", "input[type='submit']"]:
        try:
            driver.find_element(By.CSS_SELECTOR, sel).click()
            break
        except Exception:
            continue

    wait.until(lambda d: "signin" not in d.current_url.lower())
    log.info("Logged in to Tableau Server")


def _switch_to_viz_frame(driver: webdriver.Chrome):
    """Switch into the Tableau viz iframe if present."""
    driver.switch_to.default_content()
    for sel in [
        "iframe#tableau_frame",
        "iframe[id*='tableau']",
        "iframe[class*='tableau']",
        "iframe[src*='views']",
    ]:
        try:
            iframe = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            driver.switch_to.frame(iframe)
            log.info("Switched to viz iframe: %s", sel)
            return
        except TimeoutException:
            continue
    log.info("No viz iframe found — operating in main frame")


def _enter_access_key(driver: webdriver.Chrome):
    _switch_to_viz_frame(driver)
    wait = WebDriverWait(driver, _WAIT)

    # The Access Key field is the only plain text input in the filter bar
    strategies = [
        (By.XPATH, "//div[contains(@class,'tab-parameterControl')]//input[@type='text']"),
        (By.CSS_SELECTOR, "input.tab-parameterControl-text"),
        (By.XPATH, "//input[@type='text' and not(@disabled)]"),
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
    driver.switch_to.default_content()


def _click_download_crosstab(driver: webdriver.Chrome):
    _switch_to_viz_frame(driver)
    wait = WebDriverWait(driver, _WAIT)

    # Find Download toolbar button
    dl_btn = None
    for sel in [
        "[data-tb-test-id='DownloadButton-Button']",
        "button[title='Download']",
        "button[aria-label='Download']",
        ".tab-toolbar-btn-download",
        "[data-tb-test-id*='ownload']",
    ]:
        try:
            dl_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
            log.info("Found Download button via: %s", sel)
            break
        except TimeoutException:
            continue

    if dl_btn is None:
        raise RuntimeError("Could not find Download button in Tableau toolbar")

    dl_btn.click()
    time.sleep(1)

    # Click Crosstab from dropdown
    for by, sel in [
        (By.CSS_SELECTOR, "[data-tb-test-id='DownloadCrosstab-Button']"),
        (By.XPATH, "//button[normalize-space()='Crosstab']"),
        (By.XPATH, "//li[normalize-space()='Crosstab']"),
        (By.XPATH, "//span[normalize-space()='Crosstab']"),
        (By.XPATH, "//*[contains(text(),'Crosstab')]"),
    ]:
        try:
            el = wait.until(EC.element_to_be_clickable((by, sel)))
            el.click()
            log.info("Clicked Crosstab option")
            break
        except TimeoutException:
            continue

    time.sleep(1)

    # Confirm download dialog (appears in some Tableau versions)
    for by, sel in [
        (By.CSS_SELECTOR, "[data-tb-test-id='DownloadCrosstabDialog-DownloadButton']"),
        (By.XPATH, "//button[normalize-space()='Download']"),
    ]:
        try:
            btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            log.info("Confirmed download dialog")
            break
        except TimeoutException:
            pass  # No dialog — download started directly

    driver.switch_to.default_content()


def _wait_for_download(since: float) -> str | None:
    """Wait for a new xlsx to finish downloading in CATEGORY_DIR."""
    deadline = time.time() + _DOWNLOAD_TIMEOUT
    while time.time() < deadline:
        candidates = [
            os.path.join(CATEGORY_DIR, f)
            for f in os.listdir(CATEGORY_DIR)
            if f.endswith(".xlsx") and not f.startswith(".")
        ]
        recent = [p for p in candidates if os.path.getmtime(p) >= since]
        if recent:
            newest = max(recent, key=os.path.getmtime)
            # Wait until file size is stable (download complete)
            size1 = os.path.getsize(newest)
            time.sleep(2)
            size2 = os.path.getsize(newest)
            if size1 == size2 > 0:
                log.info("Downloaded: %s (%d bytes)", newest, size2)
                return newest
        time.sleep(2)
    return None


def download_category_performance() -> str | None:
    """Download Category Performance xlsx from Tableau. Returns file path or None."""
    if not all([TABLEAU_SERVER, TABLEAU_USERNAME, TABLEAU_PASSWORD, TABLEAU_ACCESS_KEY]):
        log.warning("Tableau credentials not configured — skipping download")
        return None

    os.makedirs(CATEGORY_DIR, exist_ok=True)
    start = time.time()
    driver = None
    try:
        driver = _make_driver()
        _login(driver)

        view_url = f"{TABLEAU_SERVER}/views/{_WORKBOOK}/{_SHEET}"
        driver.get(view_url)
        _wait_for_tableau(driver, extra=3)
        log.info("Loaded Category Performance view")

        _enter_access_key(driver)
        _click_download_crosstab(driver)

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
