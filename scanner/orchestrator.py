"""Generic 3-phase scan driver. Touches only adapters/base.py types and
common/db.py — no retailer-specific code lives here. This is the file a
FakeRetailerAdapter test (tests/test_fake_adapter.py) runs through to prove
the abstraction actually holds.
"""

from __future__ import annotations

import logging

from adapters.base import NeedsLogin, RetailerAdapter
from common import db
from scanner.ratelimit import RateLimiter

logger = logging.getLogger("clearance_scout.orchestrator")


class ScanAbortedNeedsLogin(Exception):
    """Raised up to main.py so it can flip credential_session to
    'needs_login' and stop scheduling scans until a human fixes it over
    noVNC, instead of retrying a doomed request in a tight loop."""


def run_scan(
    conn,
    browser_ctx,
    adapter: RetailerAdapter,
    zip_code: str,
    trigger: str = "scheduled",
    department_filter: str | None = None,
) -> dict:
    """Runs one full scan (auth check -> departments -> products -> prices)
    for one retailer/store, writing results to Postgres as it goes so a mid-
    scan crash doesn't lose everything already checked."""

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

    store_info = adapter.set_store(browser_ctx, zip_code)
    # Convention: adapters read the active store off the browser context
    # rather than threading a StoreInfo through every call — see
    # HomeDepotAdapter._require_store_id for the consuming side.
    browser_ctx.clearance_scout_store_id = store_info.retailer_store_id
    store_id = db.upsert_store(
        conn, retailer_id, store_info.retailer_store_id, store_info.zip_code,
        store_info.name, store_info.address,
    )

    products_checked = 0
    errors_count = 0
    new_deal_ids: list[int] = []

    scan_run_id = db.start_scan_run(conn, retailer_id, store_id, "departments", trigger)
    departments = list(adapter.discover_departments(browser_ctx))
    if department_filter:
        departments = [d for d in departments if d.retailer_department_id == department_filter]
    db.finish_scan_run(conn, scan_run_id, "completed", 0, 0)

    for department in departments:
        db.upsert_department(
            conn, retailer_id, department.retailer_department_id, department.name,
            parent_department_id=None,  # resolved lazily; parent linkage is a nice-to-have, not load-bearing
        )

        scan_run_id = db.start_scan_run(conn, retailer_id, store_id, "products", trigger)
        try:
            product_refs = list(adapter.list_products(browser_ctx, department))
        except Exception:
            logger.exception("Failed listing products for department %s", department.name)
            db.finish_scan_run(conn, scan_run_id, "failed", 0, 1)
            continue
        db.finish_scan_run(conn, scan_run_id, "completed", len(product_refs), 0)

        scan_run_id = db.start_scan_run(conn, retailer_id, store_id, "prices", trigger)
        for product_ref in product_refs:
            limiter.wait_before_next_request()
            try:
                observation = adapter.check_price(browser_ctx, product_ref, store_info)
                limiter.record_success()
            except PermissionError:
                limiter.record_403()
                errors_count += 1
                continue
            except Exception:
                logger.exception("Failed checking price for %s", product_ref.retailer_product_id)
                errors_count += 1
                continue

            product_id = db.upsert_product(
                conn, retailer_id, product_ref.retailer_product_id, product_ref.name,
                department_id=None, upc=product_ref.upc, image_url=product_ref.image_url,
            )
            db.upsert_store_product_location(conn, product_id, store_id, observation.aisle, observation.bay)

            observation_id = db.insert_price_observation(
                conn, product_id, store_id, scan_run_id, observation.observed_at,
                observation.price_cents, observation.list_price_cents,
                observation.is_clearance, observation.is_penny,
                observation.fulfillment_state, observation.raw_signal,
            )
            _, is_new = db.upsert_deal_from_observation(
                conn, product_id, store_id, observation_id,
                observation.is_clearance, observation.is_penny,
            )
            if is_new:
                new_deal_ids.append(product_id)

            products_checked += 1

        db.finish_scan_run(conn, scan_run_id, "completed", products_checked, errors_count)

    return {
        "departments_scanned": len(departments),
        "products_checked": products_checked,
        "errors_count": errors_count,
        "new_deal_product_ids": new_deal_ids,
    }
