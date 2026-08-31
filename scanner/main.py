"""Scanner container entrypoint: owns the one persistent, authenticated
browser identity and runs scheduled scans against it. Exposes a tiny
internal-only HTTP API (not proxied by NPM) so the web dashboard and the
Telegram bot can trigger an immediate scan or ask for status, without either
of them touching the browser directly.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from patchright.sync_api import sync_playwright

from adapters.registry import build_adapter
from common import db
from scanner.log_buffer import RingBufferLogHandler
from scanner.orchestrator import ScanAbortedNeedsLogin, run_scan

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("clearance_scout.scanner")

# Feeds the dashboard's Logs tab (see /logs below and
# web/backend/routes/logs.py) -- attached to the root logger so it captures
# everything (orchestrator, adapters, ratelimit), not just this module.
_log_buffer = RingBufferLogHandler(capacity=int(os.environ.get("LOG_BUFFER_CAPACITY", "500")))
_log_buffer.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_log_buffer)

def _split_env_list(name: str) -> list[str] | None:
    raw = os.environ.get(name, "")
    values = [s.strip() for s in raw.split(",") if s.strip()]
    return values or None  # empty/unset means "no filter, scan everything"


RETAILERS = [s.strip() for s in os.environ.get("RETAILERS", "home_depot").split(",") if s.strip()]
ZIP_CODE = os.environ["ZIP_CODE"]
RADIUS_MILES = float(os.environ.get("RADIUS_MILES", "25"))
WATCHED_DEPARTMENTS = _split_env_list("WATCHED_DEPARTMENTS")
WATCH_KEYWORDS = _split_env_list("WATCH_KEYWORDS")
SCAN_INTERVAL_MINUTES = float(os.environ.get("SCAN_INTERVAL_MINUTES", "240"))
PROFILE_DIR = os.environ.get("PLAYWRIGHT_PROFILE_DIR", "/data/browser-profile")
TRIGGER_PORT = int(os.environ.get("TRIGGER_PORT", "8090"))

_trigger_event = threading.Event()
_trigger_department: str | None = None
_status_lock = threading.Lock()
_status = {"last_scan_started_at": None, "last_scan_result": None, "state": "starting"}

app = FastAPI(title="clearance-scout scanner (internal)")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status():
    with _status_lock:
        return dict(_status)


@app.get("/logs")
def logs():
    return _log_buffer.records()


@app.post("/trigger-scan")
def trigger_scan(department: str | None = None):
    global _trigger_department
    _trigger_department = department
    _trigger_event.set()
    return {"triggered": True}


def _run_http_server():
    # Not uvicorn.run() — it installs OS signal handlers, which only works
    # in the process's main thread, and this runs in a background thread
    # alongside the Playwright scan loop on the main thread.
    config = uvicorn.Config(app, host="0.0.0.0", port=TRIGGER_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server.run()


def _scan_all(browser_ctx, trigger: str, department_filter: str | None) -> None:
    with _status_lock:
        _status["last_scan_started_at"] = datetime.now(timezone.utc).isoformat()
        _status["state"] = "scanning"

    for slug in RETAILERS:
        adapter = build_adapter(slug)
        try:
            with db.get_connection() as conn:
                result = run_scan(
                    conn, browser_ctx, adapter, ZIP_CODE, radius_miles=RADIUS_MILES,
                    trigger=trigger, department_filter=department_filter,
                    watched_departments=WATCHED_DEPARTMENTS, watch_keywords=WATCH_KEYWORDS,
                )
            logger.info("Scan complete for %s: %s", slug, result)
            with _status_lock:
                _status["last_scan_result"] = {slug: result}
        except ScanAbortedNeedsLogin:
            logger.warning(
                "%s session needs login — open the dashboard's Browser tab and log in manually. "
                "Skipping until next cycle.", slug
            )
            with _status_lock:
                _status["last_scan_result"] = {slug: "needs_login"}
        except Exception:
            # A scan failure must never crash this process. Docker's
            # restart policy has no backoff between restarts, so a crash
            # here means hammering the retailer's site again within
            # seconds, every time, with zero pacing -- exactly what the
            # rate limiter exists to prevent. Log it and wait for the next
            # scheduled interval instead.
            logger.exception("%s scan failed unexpectedly — will retry next cycle", slug)
            with _status_lock:
                _status["last_scan_result"] = {slug: "error"}

    with _status_lock:
        _status["state"] = "idle"


def main() -> None:
    threading.Thread(target=_run_http_server, daemon=True).start()

    with sync_playwright() as playwright:
        # Manual --disable-blink-features / ignore_default_args flags
        # weren't enough (confirmed live: Home Depot's real login API
        # 403'd on the very first attempt, no request volume yet -- that's
        # fingerprint-based detection, not rate-based). Those tells are
        # JS-observable; the deeper leak is the CDP connection itself
        # (Runtime.enable), which no amount of launch-arg tweaking touches.
        # Patchright patches that at the driver level instead of the
        # browser-args level -- this is its own documented "best practice"
        # config (real Chrome via channel="chrome", no_viewport, no custom
        # UA/headers); it already handles the automation-flag patching
        # internally, more thoroughly than the manual flags did.
        #
        # No --load-extension here: Patchright silently strips that flag
        # (and --disable-extensions-except) as part of its own anti-detection
        # arg sanitization (confirmed via isolated diagnostic), so passing it
        # is a no-op, not a degraded fallback. adapters/home_depot no longer
        # depends on a loaded extension either way -- see api_client.py.
        browser_ctx = playwright.chromium.launch_persistent_context(
            PROFILE_DIR,
            channel="chrome",
            headless=False,  # a real, visible browser inside the container's Xvfb display
            no_viewport=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        logger.info("Persistent browser context ready (profile: %s)", PROFILE_DIR)

        while True:
            department_filter = None
            if _trigger_event.is_set():
                department_filter = _trigger_department
                _trigger_event.clear()
                _scan_all(browser_ctx, trigger="manual", department_filter=department_filter)
            else:
                _scan_all(browser_ctx, trigger="scheduled", department_filter=None)

            # Sleep in short increments so a trigger during the wait is
            # picked up promptly instead of after the full interval.
            deadline = time.monotonic() + SCAN_INTERVAL_MINUTES * 60
            while time.monotonic() < deadline:
                if _trigger_event.wait(timeout=5):
                    break


if __name__ == "__main__":
    main()
