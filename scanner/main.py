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
from scanner.orchestrator import ScanAbortedNeedsLogin, refresh_single_product, repair_missing_enrichment, run_scan
from scanner.settings import merge_settings, split_list

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("clearance_scout.scanner")

# Feeds the dashboard's Logs tab (see /logs below and
# web/backend/routes/logs.py) -- attached to the root logger so it captures
# everything (orchestrator, adapters, ratelimit), not just this module.
_log_buffer = RingBufferLogHandler(capacity=int(os.environ.get("LOG_BUFFER_CAPACITY", "500")))
_log_buffer.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_log_buffer)

# RETAILERS/PROFILE_DIR/TRIGGER_PORT are infra-level, not scan config --
# not exposed as dashboard-editable. ENV_DEFAULTS below is only the
# *env-var default* set: _current_settings() overlays any override saved
# from the dashboard's Settings tab on top of it (scanner/settings.py's
# merge_settings -- kept there, not here, so it's testable without
# patchright being importable), read fresh on every scan/status check so
# a saved change takes effect on the next scan without needing a redeploy.
# See db/init/001_schema.sql's scanner_settings table and common/db.py's
# get_/upsert_scanner_settings.
RETAILERS = [s.strip() for s in os.environ.get("RETAILERS", "home_depot").split(",") if s.strip()]
ENV_DEFAULTS = {
    "zip_code": os.environ["ZIP_CODE"],
    "radius_miles": float(os.environ.get("RADIUS_MILES", "25")),
    "watch_keywords": split_list(os.environ.get("WATCH_KEYWORDS")),
    "product_list_cache_hours": float(os.environ.get("PRODUCT_LIST_CACHE_HOURS", "24")),
}
PROFILE_DIR = os.environ.get("PLAYWRIGHT_PROFILE_DIR", "/data/browser-profile")
TRIGGER_PORT = int(os.environ.get("TRIGGER_PORT", "8090"))


def _current_settings(retailer_id: int) -> dict:
    """Env-var defaults (ENV_DEFAULTS) overlaid with whatever's saved in
    this retailer's scanner_settings row -- called fresh at the start of
    every scan and by /config, so a change saved from the dashboard
    applies to the very next scan, no redeploy."""
    override = None
    try:
        with db.get_connection() as conn:
            override = db.get_scanner_settings(conn, retailer_id)
    except Exception:
        logger.exception("Failed to read scanner settings override -- using env-var defaults")

    return merge_settings(ENV_DEFAULTS, override)

_trigger_event = threading.Event()
_trigger_department: str | None = None
_trigger_store_ids: list[str] | None = None
_status_lock = threading.Lock()
_status = {"last_scan_started_at": None, "last_scan_result": None, "state": "starting"}
# Live checkpoint progress (store/department/counts) for the in-progress
# scan -- see orchestrator.py's on_progress. Separate from _status's
# start/end-of-scan summary; cleared at the start of each scan.
_progress: dict = {}

# The "repair missing data" tool -- a separate trigger/status pair from
# the scan ones above since it's a genuinely different operation (no
# department discovery, no price checks), sharing only the same
# persistent browser_ctx and the same "one operation at a time" main loop.
_repair_trigger_event = threading.Event()
_repair_limit: int | None = None
_repair_status = {"state": "idle", "last_run_result": None}
DEFAULT_REPAIR_LIMIT = 50  # an on-demand tool hitting a real retailer API -- default to a conservative batch, not "however many are missing."

# "Refresh this one item everywhere" -- a real QUEUE (list), not a single
# trigger flag like the ones above, specifically so someone can "mash the
# button" across many products from the dashboard without a second click
# clobbering the first one's request. _refresh_status is keyed per
# product_id so each dashboard row can poll its own outcome independently
# instead of a single shared status blowing away another row's result.
_refresh_queue: list[int] = []
_refresh_status: dict[int, dict] = {}

app = FastAPI(title="clearance-scout scanner (internal)")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status():
    with _status_lock:
        return {**_status, "progress": dict(_progress)}


@app.get("/logs")
def logs():
    return _log_buffer.records()


@app.get("/config")
def config(retailer: str):
    # Non-secret runtime config for one retailer, editable from the
    # dashboard's Settings tab (writes go straight to Postgres from the web
    # backend -- see web/backend/routes/settings.py -- this scanner
    # container has no write endpoint of its own) -- feeds the display so
    # "what is this actually scanning right now" doesn't require SSHing in
    # and reading .env by hand (confirmed real friction this session).
    # Nothing here is a credential; TELEGRAM_BOT_TOKEN etc. never get
    # exposed this way. A retailer slug with no DB row yet (never scanned)
    # falls back to pure env defaults -- nothing's been saved for it either
    # way.
    row = None
    try:
        with db.get_connection() as conn:
            row = db.get_retailer_by_slug(conn, retailer)
    except Exception:
        logger.exception("Failed to look up retailer %r -- using env-var defaults", retailer)

    settings = _current_settings(row["id"]) if row else merge_settings(ENV_DEFAULTS, None)
    return {"retailers": RETAILERS, "enabled": row["enabled"] if row else True, **settings}


@app.post("/trigger-scan")
def trigger_scan(department: str | None = None, store_ids: list[str] | None = None):
    global _trigger_department, _trigger_store_ids
    _trigger_department = department
    _trigger_store_ids = store_ids
    _trigger_event.set()
    return {"triggered": True}


@app.post("/repair-missing-data")
def repair_missing_data(limit: int | None = DEFAULT_REPAIR_LIMIT):
    # limit=0 or a negative value would otherwise slip through to
    # get_deals_missing_enrichment as "no limit" (Python truthiness on 0
    # is falsy, but the DB layer checks `is not None`) -- reject instead
    # of silently running unbounded.
    if limit is not None and limit <= 0:
        return {"triggered": False, "error": "limit must be positive (or omitted for DEFAULT_REPAIR_LIMIT)"}
    global _repair_limit
    _repair_limit = limit
    _repair_trigger_event.set()
    return {"triggered": True}


@app.get("/repair-status")
def repair_status():
    with _status_lock:
        return dict(_repair_status)


@app.post("/refresh-product")
def refresh_product(product_id: int):
    with _status_lock:
        already_pending = product_id in _refresh_queue or _refresh_status.get(product_id, {}).get("state") == "running"
        if already_pending:
            return {"queued": False, "reason": "already queued or in progress"}
        _refresh_queue.append(product_id)
        _refresh_status[product_id] = {"state": "queued", "result": None}
        position = len(_refresh_queue)
    return {"queued": True, "position": position}


@app.get("/refresh-status")
def refresh_status(product_id: int):
    with _status_lock:
        return _refresh_status.get(product_id, {"state": "unknown", "result": None})


def _run_http_server():
    # Not uvicorn.run() — it installs OS signal handlers, which only works
    # in the process's main thread, and this runs in a background thread
    # alongside the Playwright scan loop on the main thread.
    config = uvicorn.Config(app, host="0.0.0.0", port=TRIGGER_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server.run()


def _scan_all(browser_ctx, trigger: str, department_filter: str | None, recycle_browser_ctx, store_ids: list[str] | None = None):
    """Returns the (possibly recycled) browser_ctx -- run_scan() may swap
    it out mid-scan (see recycle_browser_ctx), so the caller's own
    reference has to be updated from the result, not assumed unchanged."""
    with _status_lock:
        _status["last_scan_started_at"] = datetime.now(timezone.utc).isoformat()
        _status["state"] = "scanning"
        _progress.clear()

    def _on_progress(fields: dict) -> None:
        with _status_lock:
            _progress.update(fields)

    for slug in RETAILERS:
        adapter = build_adapter(slug)
        try:
            with db.get_connection() as conn:
                # No row yet == never scanned before -- proceed with pure
                # env defaults and no restrictions (nothing's been saved
                # to restrict it with). A row exists but enabled=false ==
                # deliberately paused in Settings -- skip entirely.
                retailer_row = db.get_retailer_by_slug(conn, slug)
                if retailer_row is not None and not retailer_row["enabled"]:
                    logger.info("%s is disabled in Settings -- skipping", slug)
                    with _status_lock:
                        _status["last_scan_result"] = {slug: "disabled"}
                    continue
                settings = (
                    _current_settings(retailer_row["id"]) if retailer_row is not None
                    else merge_settings(ENV_DEFAULTS, None)
                )
                watched_department_names = (
                    db.get_watched_department_names(conn, retailer_row["id"])
                    if retailer_row is not None else None
                )
                result = run_scan(
                    conn, browser_ctx, adapter, settings["zip_code"], radius_miles=settings["radius_miles"],
                    trigger=trigger, department_filter=department_filter, store_ids=store_ids,
                    watched_department_names=watched_department_names, watch_keywords=settings["watch_keywords"],
                    product_list_cache_hours=settings["product_list_cache_hours"],
                    recycle_browser_ctx=recycle_browser_ctx,
                    on_progress=_on_progress,
                )
            browser_ctx = result.pop("browser_ctx", browser_ctx)
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

    return browser_ctx


def _repair_all(browser_ctx, limit: int | None):
    with _status_lock:
        _repair_status["state"] = "running"

    for slug in RETAILERS:
        adapter = build_adapter(slug)
        try:
            with db.get_connection() as conn:
                result = repair_missing_enrichment(conn, browser_ctx, adapter, limit=limit)
            logger.info("Repair complete for %s: %s", slug, result)
            with _status_lock:
                _repair_status["last_run_result"] = {slug: result}
        except Exception:
            # Same posture as _scan_all's except-clause -- never crash the
            # process over a single bad run.
            logger.exception("%s repair failed unexpectedly", slug)
            with _status_lock:
                _repair_status["last_run_result"] = {slug: "error"}

    with _status_lock:
        _repair_status["state"] = "idle"

    return browser_ctx


def _refresh_one(browser_ctx, product_id: int):
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT r.slug FROM product p JOIN retailer r ON r.id = p.retailer_id WHERE p.id = %s",
            (product_id,),
        ).fetchone()
    if not row:
        logger.warning("Refresh: no product with id %s", product_id)
        with _status_lock:
            _refresh_status[product_id] = {"state": "error", "result": None}
        return browser_ctx

    adapter = build_adapter(row["slug"])
    try:
        with db.get_connection() as conn:
            result = refresh_single_product(conn, browser_ctx, adapter, product_id)
        logger.info("Refresh complete for product %s: %s", product_id, result)
        with _status_lock:
            _refresh_status[product_id] = {"state": "done", "result": result}
    except Exception:
        # Same posture as _scan_all/_repair_all -- never crash the process
        # over a single bad run; the next queued (or re-clicked) refresh
        # gets a clean attempt regardless of this one's outcome.
        logger.exception("Refresh failed for product %s", product_id)
        with _status_lock:
            _refresh_status[product_id] = {"state": "error", "result": None}

    return browser_ctx


def _launch_browser_ctx(playwright):
    # Manual --disable-blink-features / ignore_default_args flags weren't
    # enough (confirmed live: Home Depot's real login API 403'd on the
    # very first attempt, no request volume yet -- that's fingerprint-based
    # detection, not rate-based). Those tells are JS-observable; the deeper
    # leak is the CDP connection itself (Runtime.enable), which no amount
    # of launch-arg tweaking touches. Patchright patches that at the driver
    # level instead of the browser-args level -- this is its own documented
    # "best practice" config (real Chrome via channel="chrome", no_viewport,
    # no custom UA/headers); it already handles the automation-flag
    # patching internally, more thoroughly than the manual flags did.
    #
    # No --load-extension here: Patchright silently strips that flag (and
    # --disable-extensions-except) as part of its own anti-detection arg
    # sanitization (confirmed via isolated diagnostic), so passing it is a
    # no-op, not a degraded fallback. adapters/home_depot no longer depends
    # on a loaded extension either way -- see api_client.py.
    return playwright.chromium.launch_persistent_context(
        PROFILE_DIR,
        channel="chrome",
        headless=False,  # a real, visible browser inside the container's Xvfb display
        no_viewport=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )


def main() -> None:
    threading.Thread(target=_run_http_server, daemon=True).start()

    with sync_playwright() as playwright:
        browser_ctx = _launch_browser_ctx(playwright)
        logger.info("Persistent browser context ready (profile: %s)", PROFILE_DIR)

        def recycle_browser_ctx(old_ctx):
            # Bounds the Patchright driver's per-request memory growth
            # (confirmed live 2026-08-31/09-01, roughly linear with total
            # request count, root cause still unresolved -- GitHub issue
            # #4) to one store's worth of requests instead of letting it
            # compound across an entire multi-store scan. Safe to do
            # freely: this retailer doesn't require a logged-in session
            # (see adapter.py's authenticate()), so there's no session
            # state a fresh context would lose.
            logger.info("Recycling browser context (new store) to bound per-scan memory growth")
            try:
                old_ctx.close()
            except Exception:
                logger.exception("Error closing old browser context (continuing anyway)")
            return _launch_browser_ctx(playwright)

        while True:
            if _refresh_queue:
                # Ahead of repair/scan -- a single-item refresh is small,
                # quick, and directly user-initiated ("I'm looking at this
                # right now"), so it shouldn't queue behind a long repair
                # or scan run that's about to start.
                with _status_lock:
                    product_id = _refresh_queue.pop(0)
                    _refresh_status[product_id] = {"state": "running", "result": None}
                browser_ctx = _refresh_one(browser_ctx, product_id)
            elif _repair_trigger_event.is_set():
                limit = _repair_limit
                _repair_trigger_event.clear()
                browser_ctx = _repair_all(browser_ctx, limit)
            elif _trigger_event.is_set():
                department_filter = _trigger_department
                store_ids = _trigger_store_ids
                _trigger_event.clear()
                browser_ctx = _scan_all(
                    browser_ctx, trigger="manual", department_filter=department_filter, store_ids=store_ids,
                    recycle_browser_ctx=recycle_browser_ctx,
                )
            else:
                # No automatic scanning -- not on a timer, not on
                # container start. A scan is a heavy operation that makes
                # real requests against the retailer; it only ever runs
                # because a human asked for it (dashboard "Scan now" / bot
                # /scan). Idle and poll for the next trigger.
                with _status_lock:
                    _status["state"] = "idle"
                time.sleep(5)


if __name__ == "__main__":
    main()
