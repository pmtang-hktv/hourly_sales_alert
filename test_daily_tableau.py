"""Download the Daily Sales Update crosstab from Tableau and print its contents.

Usage:
  TABLEAU_HEADLESS=false python3 test_daily_tableau.py [YYYY-MM-DD]

  Date defaults to yesterday. The view auto-shows yesterday's data — no date
  filter manipulation needed.
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

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

from src.config import TABLEAU_HEADLESS, TABLEAU_PASSWORD, TABLEAU_SERVER, TABLEAU_USERNAME

DOWNLOAD_DIR = str(Path("data/daily_test").resolve())
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

WORKBOOK = "DailySalesUpdate-BQSimplfied"
SHEET    = "DailySalesUpdate"
VIEW_URL = f"{TABLEAU_SERVER}/views/{WORKBOOK}/{SHEET}"

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

# ── 1. Login (navigate directly to view — Tableau redirects through signin) ──
log.info("Logging in...")
driver.get(VIEW_URL)
f = WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.CSS_SELECTOR,
    "input#username, input[name='username'], input[autocomplete='username'], input[type='text']"
)))
f.clear(); f.send_keys(TABLEAU_USERNAME)
p = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR,
    "input#password, input[name='password'], input[type='password']"
)))
p.clear(); p.send_keys(TABLEAU_PASSWORD)
WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR,
    "button[type='submit'], button.signin-btn, #signin-btn, input[type='submit']"
))).click()
WebDriverWait(driver, 30).until(lambda d: "signin" not in d.current_url.lower())
log.info("Logged in")

# ── 2. Wait for the view to load ─────────────────────────────────────────────
log.info("Loading Daily Sales Update view...")
wait_for_tableau(extra=2)
switch_to_viz_frame()
log.info("View ready")

# ── 3. Download crosstab ─────────────────────────────────────────────────────
log.info("Downloading crosstab...")
w = WebDriverWait(driver, 30)

dl_btn = None
for by, sel in [
    (By.CSS_SELECTOR, "[data-tb-test-id='DownloadButton-Button']"),
    (By.CSS_SELECTOR, "button[title='Download']"),
    (By.CSS_SELECTOR, "button[aria-label='Download']"),
    (By.XPATH, "//button[contains(@aria-label,'下載') or contains(@title,'下載')]"),
    (By.CSS_SELECTOR, "[data-tb-test-id*='ownload']"),
]:
    try:
        dl_btn = w.until(EC.element_to_be_clickable((by, sel)))
        log.info("Download button: %s", sel)
        break
    except TimeoutException:
        continue

if not dl_btn:
    log.error("Download button not found")
    driver.quit()
    sys.exit(1)

dl_btn.click()
time.sleep(1)

for by, sel in [
    (By.CSS_SELECTOR, "[data-tb-test-id='DownloadCrosstab-Button']"),
    (By.XPATH, "//*[normalize-space()='交叉資料表']"),
    (By.XPATH, "//*[normalize-space()='Crosstab']"),
]:
    try:
        w.until(EC.element_to_be_clickable((by, sel))).click()
        log.info("Clicked Crosstab")
        break
    except TimeoutException:
        continue

time.sleep(1.5)

for by, sel in [
    (By.CSS_SELECTOR, "[data-tb-test-id='export-crosstab-export-Button']"),
    (By.XPATH, "//button[normalize-space()='下載']"),
    (By.XPATH, "//button[normalize-space()='Download']"),
]:
    try:
        w.until(EC.element_to_be_clickable((by, sel))).click()
        log.info("Clicked 下載")
        break
    except TimeoutException:
        continue

# ── 4. Wait for download to complete ─────────────────────────────────────────
log.info("Waiting for file...")
start = time.time()
downloaded = None
while time.time() - start < 90:
    partials = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith(".crdownload")]
    done = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith(".xlsx")]
    if done and not partials:
        downloaded = os.path.join(DOWNLOAD_DIR, done[-1])
        time.sleep(1)
        log.info("Downloaded: %s (%d bytes)", downloaded, os.path.getsize(downloaded))
        break
    time.sleep(2)
else:
    log.error("Download timed out")

driver.quit()

# ── 5. Print all rows so we can see the column structure ─────────────────────
if downloaded:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(downloaded)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            print(f"\n=== Sheet: {sheet_name!r} ({ws.max_row} rows × {ws.max_column} cols) ===")
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    print(row)
    except ImportError:
        log.info("pip install openpyxl  — then re-run to print contents")
