"""Generic 3-phase scan driver. Touches only adapters/base.py types and
common/db.py — no retailer-specific code lives here. This is the file the
fake-adapter tests (tests/test_fake_adapter.py, test_orchestrator_filters.py,
test_multi_store.py) run through to prove the abstraction actually holds.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from adapters.base import Department, NeedsLogin, ProductRef, RetailerAdapter
from common import db
from scanner.ratelimit import RateLimiter

logger = logging.getLogger("clearance_scout.orchestrator")


class ScanAbortedNeedsLogin(Exception):
    """Raised up to main.py so it can flip credential_session to
    'needs_login' and stop scheduling scans until a human fixes it over
    noVNC, instead of retrying a doomed request in a tight loop."""


def _matches_any(name: str, substrings: list[str] | None) -> bool:
    if not substrings:
        return True
    lowered = name.lower()
    return any(s.lower() in lowered for s in substrings)


def _select_departments(
    all_departments: list[Department],
    watched_departments: list[str] | None,
    department_filter: str | None,
) -> list[Department]:
    if department_filter:
        # An explicit single-department trigger (dashboard/bot "/scan
        # <department>") is a manual override — it applies even to a
        # department outside the configured watch list.
        return [d for d in all_departments if d.retailer_department_id == department_filter]
    return [d for d in all_departments if _matches_any(d.name, watched_departments)]


def run_scan(
    conn,
    browser_ctx,
    adapter: RetailerAdapter,
    zip_code: str,
    radius_miles: float = 25.0,
    trigger: str = "scheduled",
    department_filter: str | None = None,
    watched_departments: list[str] | None = None,
    watch_keywords: list[str] | None = None,
    product_list_cache_hours: float = 24.0,
) -> dict:
    """Runs one full scan (auth check -> stores in radius -> departments ->
    products -> prices) for one retailer, writing results to Postgres as it
    goes so a mid-scan crash doesn't lose everything already checked.

    `watched_departments` / `watch_keywords` narrow what actually gets
    requested from the retailer (fewer departments listed, fewer products
    price-checked) — not just what the dashboard displays afterward. Both
    are case-insensitive substring matches; leave unset to scan everything
    (the original, unfiltered behavior).

    `product_list_cache_hours`: phase 2 (which products exist in a
    department) is request-heavy (multiple paginated calls per department)
    but changes far less often than phase 3 (current price/clearance,
    which must always be checked fresh -- that's the entire point of a
    scan). Re-listing on every single scan was pure waste -- confirmed
    live 2026-08-31, a big contributor to a multi-hour scan across 71
    departments that led to a memory-exhaustion incident (GitHub issue #4).
    A department already listed within this window is served from the
    `product` table (see common/db.py's product-ID cache) instead of
    re-querying the retailer.
    """

    retailer_id = db.upsert_retailer(conn, adapter.retailer_slug, adapter.retailer_slug, "")
    limiter = RateLimiter(
        policy=adapter.rate_limit_policy(),
        on_event=lambda event_type, detail: db.record_rate_limit_event(conn, retailer_id, event_type, detail),
    )

    try:
        adapter.authenticate(browser_ctx)
    except NeedsLogin:
        db.set_credential_session_status(conn, retailer_id, "needs_login")
        raise ScanAbortedNeedsLogin()
    db.set_credential_session_status(conn, retailer_id, "valid")

    stores = list(adapter.find_stores(browser_ctx, zip_code, radius_miles))
    # Progress logging below is deliberately checkpoint-based (store,
    # department, and a periodic heartbeat during price checks), not
    # per-item -- confirmed live 2026-08-31 the orchestrator previously
    # logged nothing at all on the success path, only on failures, which
    # left the dashboard's Logs tab empty for the entire length of a scan.
    # Per-item logging would also blow past the scanner's 500-line ring
    # buffer (scanner/log_buffer.py) well before a real scan finishes.
    logger.info("%s: found %d store(s) within %s miles of %s", adapter.retailer_slug, len(stores), radius_miles, zip_code)

    stores_scanned = 0
    departments_scanned = 0
    products_checked = 0
    errors_count = 0
    new_deal_ids: list[int] = []

    for store_info in stores:
        adapter.select_store(browser_ctx, store_info)
        browser_ctx.clearance_scout_store_id = store_info.retailer_store_id
        store_id = db.upsert_store(
            conn, retailer_id, store_info.retailer_store_id, store_info.zip_code,
            store_info.name, store_info.address,
        )
        stores_scanned += 1
        logger.info("Store %s (%s): scanning", store_info.name or store_info.retailer_store_id, store_info.retailer_store_id)

        scan_run_id = db.start_scan_run(conn, retailer_id, store_id, "departments", trigger)
        all_departments = list(adapter.discover_departments(browser_ctx))
        departments = _select_departments(all_departments, watched_departments, department_filter)
        db.finish_scan_run(conn, scan_run_id, "completed", 0, 0)
        logger.info(
            "Store %s: %d department(s) discovered, %d match the watch list",
            store_info.retailer_store_id, len(all_departments), len(departments),
        )

        for department in departments:
            department_id = db.upsert_department(
                conn, retailer_id, department.retailer_department_id, department.name,
                parent_department_id=None,  # resolved lazily; parent linkage is a nice-to-have, not load-bearing
            )
            departments_scanned += 1

            last_listed_at = db.get_department_products_last_listed_at(conn, department_id)
            cache_is_fresh = (
                last_listed_at is not None
                and datetime.now(timezone.utc) - last_listed_at < timedelta(hours=product_list_cache_hours)
            )

            scan_run_id = db.start_scan_run(conn, retailer_id, store_id, "products", trigger)
            if cache_is_fresh:
                all_product_refs = [
                    ProductRef(
                        retailer_product_id=row["retailer_product_id"], name=row["name"],
                        department=department, upc=row["upc"], image_url=row["image_url"],
                    )
                    for row in db.list_cached_products_for_department(conn, department_id)
                ]
            else:
                try:
                    all_product_refs = list(adapter.list_products(browser_ctx, department))
                except Exception:
                    logger.exception("Failed listing products for department %s", department.name)
                    db.finish_scan_run(conn, scan_run_id, "failed", 0, 1)
                    continue
                db.mark_department_products_listed(conn, department_id)
            product_refs = [p for p in all_product_refs if _matches_any(p.name, watch_keywords)]
            db.finish_scan_run(conn, scan_run_id, "completed", len(product_refs), 0)
            logger.info(
                "Department %r: %d product(s) to check (%s)",
                department.name, len(product_refs),
                "from cache" if cache_is_fresh else "freshly listed",
            )

            scan_run_id = db.start_scan_run(conn, retailer_id, store_id, "prices", trigger)
            department_hits = 0
            department_errors = 0
            for i, product_ref in enumerate(product_refs, start=1):
                limiter.wait_before_next_request()
                try:
                    observation = adapter.check_price(browser_ctx, product_ref, store_info)
                    limiter.record_success()
                except PermissionError:
                    limiter.record_403()
                    errors_count += 1
                    department_errors += 1
                    continue
                except Exception:
                    logger.exception("Failed checking price for %s", product_ref.retailer_product_id)
                    errors_count += 1
                    department_errors += 1
                    continue

                product_id = db.upsert_product(
                    conn, retailer_id, product_ref.retailer_product_id, product_ref.name,
                    department_id=department_id, upc=product_ref.upc,
                    image_url=observation.image_url or product_ref.image_url,
                    canonical_url=observation.canonical_url,
                )
                db.upsert_store_product_location(conn, product_id, store_id, observation.aisle, observation.bay)

                observation_id = db.insert_price_observation(
                    conn, product_id, store_id, scan_run_id, observation.observed_at,
                    observation.price_cents, observation.list_price_cents,
                    observation.is_clearance, observation.is_penny,
                    observation.fulfillment_state, observation.stock_quantity, observation.raw_signal,
                )
                _, is_new = db.upsert_deal_from_observation(
                    conn, product_id, store_id, observation_id,
                    observation.is_clearance, observation.is_penny,
                )
                if is_new:
                    new_deal_ids.append(product_id)
                if observation.is_clearance or observation.is_penny:
                    department_hits += 1
                    logger.info(
                        "%s Found: %s at $%.2f%s",
                        "\U0001f7e1" if observation.is_clearance else "\U0001fa99",
                        product_ref.name, observation.price_cents / 100,
                        " (new)" if is_new else "",
                    )

                products_checked += 1

                # Heartbeat every 10 items so a long department still shows
                # live progress, not just a start/end log line.
                if i % 10 == 0 and i != len(product_refs):
                    logger.info("Department %r: checked %d/%d so far", department.name, i, len(product_refs))

            db.finish_scan_run(conn, scan_run_id, "completed", products_checked, errors_count)
            logger.info(
                "Department %r: done -- %d checked, %d hit(s), %d error(s)",
                department.name, len(product_refs), department_hits, department_errors,
            )

    return {
        "stores_scanned": stores_scanned,
        "departments_scanned": departments_scanned,
        "products_checked": products_checked,
        "errors_count": errors_count,
        "new_deal_product_ids": new_deal_ids,
    }
