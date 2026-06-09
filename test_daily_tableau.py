"""Quick exploration script — downloads the Daily Sales Update crosstab from Tableau
for a given date and prints the file contents so we can see what columns are available.

Usage:
  TABLEAU_HEADLESS=false python3 test_daily_tableau.py [YYYY-MM-DD]

  Date defaults to yesterday. Set TABLEAU_HEADLESS=false to watch the browser.
"""
from __future__ import annotations

import os
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("test_daily_tableau")

# ── date arg ──────────────────────────────────────────────────────────────────
if len(sys.argv) > 1:
    target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
else:
    target = (datetime.now() - timedelta(days=1)).date()

log.info("Target date: %s", target)

# ── Selenium setup (mirrors tableau_downloader._make_driver) ──────────────────
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

from src.config import CATEGORY_DIR, TABLEAU_HEADLESS, TABLEAU_PASSWORD, TABLEAU_SERVER, TABLEAU_USERNAME

DOWNLOAD_DIR = str(Path("data/daily_test").resolve())
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

WORKBOOK = "DailySalesUpdate-BQSimplfied"
SHEET    = "DailySalesUpdate"

opts = Options()
opts.page_load_strategy = "eager"
if TABLEAU_HEADLESS:
    opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1920,1080")
opts.add_experimental_option("prefs", {
    "download.default_directory": DOWNLOAD_DIR,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "safebrowsing.enabled": False,
})
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
wait = WebDriverWait(driver, 30)

def wait_for_tableau(extra=2.0):
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            if not driver.execute_script(
                "return !!document.querySelector('.tab-viz-loading,.tabBootProgressBar,.tb-loading-indicator')"
            ):
                break
        except Exception:
            break
        time.sleep(1)
    time.sleep(extra)

def switch_to_viz_frame():
    driver.switch_to.default_content()
    for sel in ["iframe#tableau_frame", "iframe[id*='tableau']", "iframe[src*='views']"]:
        try:
            frame = WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            driver.switch_to.frame(frame)
            log.info("In viz frame: %s", sel)
            return
        except TimeoutException:
            continue
    log.info("No viz iframe — staying in main frame")

def screenshot(label):
    path = f"data/daily_test/screenshot_{label}.png"
    driver.save_screenshot(path)
    log.info("Screenshot: %s", path)

# ── 1. Login ──────────────────────────────────────────────────────────────────
log.info("Logging in...")
driver.get(f"{TABLEAU_SERVER}/#/signin")
for sel in ["input#username", "input[name='username']", "input[type='text']"]:
    try:
        f = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
        f.clear(); f.send_keys(TABLEAU_USERNAME); break
    except TimeoutException:
        continue
for sel in ["input#password", "input[type='password']"]:
    try:
        f = driver.find_element(By.CSS_SELECTOR, sel)
        f.clear(); f.send_keys(TABLEAU_PASSWORD); break
    except Exception:
        continue
for sel in ["button[type='submit']", "button.signin-btn", "#signin-btn"]:
    try:
        driver.find_element(By.CSS_SELECTOR, sel).click(); break
    except Exception:
        continue
wait.until(lambda d: "signin" not in d.current_url.lower())
log.info("Logged in")

# ── 2. Open the Daily Sales Update view ───────────────────────────────────────
log.info("Opening Daily Sales Update view...")
driver.get(f"{TABLEAU_SERVER}/views/{WORKBOOK}/{SHEET}")
wait_for_tableau(extra=3)
switch_to_viz_frame()
screenshot("01_loaded")

# ── 3. Dump all inputs so we can see the date filter structure ─────────────────
import re
DATE_RE = re.compile(r"\d{4}/\d+/\d+|\d{1,2}/\d{1,2}/\d{4}")

log.info("=== ALL INPUTS ===")
for i, inp in enumerate(driver.find_elements(By.TAG_NAME, "input")):
    try:
        log.info("  [%d] type=%s class=%s value=%r displayed=%s rect=%s",
            i, inp.get_attribute("type"), inp.get_attribute("class"),
            inp.get_attribute("value"), inp.is_displayed(), inp.rect)
    except Exception as e:
        log.info("  [%d] error: %s", i, e)

log.info("=== DATE-LIKE TEXT ELEMENTS ===")
for el in driver.find_elements(By.XPATH, "//*[not(self::script)][not(self::style)]"):
    try:
        txt = (el.text or "").strip()
        if txt and DATE_RE.search(txt) and len(txt) < 30 and el.is_displayed():
            log.info("  tag=%-10s class=%-30s text=%r", el.tag_name, el.get_attribute("class") or "", txt)
    except Exception:
        pass

# ── 4. Try downloading the crosstab as-is (whatever date is currently showing) ─
log.info("Attempting crosstab download...")
dl_btn = None
for by, sel in [
    (By.CSS_SELECTOR, "[data-tb-test-id='DownloadButton-Button']"),
    (By.CSS_SELECTOR, "button[title='Download']"),
    (By.CSS_SELECTOR, "button[aria-label='Download']"),
    (By.XPATH, "//button[contains(@aria-label,'下載') or contains(@title,'下載')]"),
    (By.CSS_SELECTOR, "[data-tb-test-id*='ownload']"),
]:
    try:
        dl_btn = wait.until(EC.element_to_be_clickable((by, sel)))
        log.info("Download button found: %s", sel)
        break
    except TimeoutException:
        continue

if dl_btn:
    dl_btn.click()
    time.sleep(1)
    screenshot("02_download_menu")

    for by, sel in [
        (By.CSS_SELECTOR, "[data-tb-test-id='DownloadCrosstab-Button']"),
        (By.XPATH, "//*[normalize-space()='交叉資料表']"),
        (By.XPATH, "//*[normalize-space()='Crosstab']"),
    ]:
        try:
            wait.until(EC.element_to_be_clickable((by, sel))).click()
            log.info("Clicked Crosstab")
            break
        except TimeoutException:
            continue

    time.sleep(1.5)
    screenshot("03_crosstab_dialog")

    for by, sel in [
        (By.CSS_SELECTOR, "[data-tb-test-id='export-crosstab-export-Button']"),
        (By.XPATH, "//button[normalize-space()='下載']"),
        (By.XPATH, "//button[normalize-space()='Download']"),
    ]:
        try:
            wait.until(EC.element_to_be_clickable((by, sel))).click()
            log.info("Clicked 下載")
            break
        except TimeoutException:
            continue

    # Wait for download
    log.info("Waiting for download...")
    start = time.time()
    while time.time() - start < 60:
        files = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith(".xlsx") and not f.endswith(".crdownload")]
        if files:
            path = os.path.join(DOWNLOAD_DIR, files[-1])
            time.sleep(2)
            log.info("Downloaded: %s (%d bytes)", path, os.path.getsize(path))
            break
        time.sleep(2)
    else:
        log.warning("Download timed out")
else:
    log.warning("No Download button found — check screenshot_01_loaded.png")

driver.quit()

# ── 5. Print file contents if downloaded ──────────────────────────────────────
xlsx_files = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith(".xlsx")]
if xlsx_files:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx_files[-1])
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            print(f"\n=== Sheet: {sheet_name} ({ws.max_row} rows x {ws.max_column} cols) ===")
            for row in ws.iter_rows(min_row=1, max_row=min(20, ws.max_row), values_only=True):
                if any(v is not None for v in row):
                    print(row)
    except ImportError:
        log.info("openpyxl not installed — run: pip install openpyxl")
        log.info("File saved at: %s", xlsx_files[-1])
else:
    log.info("No xlsx downloaded — check screenshots in data/daily_test/")
