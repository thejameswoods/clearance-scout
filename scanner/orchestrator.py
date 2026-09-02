"""Generic 3-phase scan driver. Touches only adapters/base.py types and
common/db.py — no retailer-specific code lives here. This is the file the
fake-adapter tests (tests/test_fake_adapter.py, test_orchestrator_filters.py,
test_multi_store.py) run through to prove the abstraction actually holds.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from adapters.base import Department, NeedsLogin, ProductRef, RetailerAdapter, StoreInfo
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
    store_ids: list[str] | None = None,
    watched_departments: list[str] | None = None,
    watch_keywords: list[str] | None = None,
    product_list_cache_hours: float = 24.0,
    recycle_browser_ctx: Callable[[object], object] | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Runs one full scan (auth check -> stores in radius -> departments ->
    products -> prices) for one retailer, writing results to Postgres as it
    goes so a mid-scan crash doesn't lose everything already checked.

    `watched_departments` / `watch_keywords` narrow what actually gets
    requested from the retailer (fewer departments listed, fewer products
    price-checked) — not just what the dashboard displays afterward. Both
    are case-insensitive substring matches; leave unset to scan everything
    (the original, unfiltered behavior).

    `store_ids`: restricts the scan to just these `StoreInfo.retailer_store_id`
    values out of what `find_stores()` returns, e.g. the dashboard's "Scan
    Now" dialog scoping to a hand-picked subset of stores. A store left out
    isn't upserted on this run either, so its `store` row (distance/name/
    address) only refreshes on a scan that does include it. None (the
    default) scans every discovered store, the original behavior.

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

    `recycle_browser_ctx`: given `browser_ctx`, returns a fresh replacement
    (closing the old one) -- called once per store. Confirmed live
    2026-08-31/09-01 that Patchright's driver process leaks memory roughly
    linearly with total request count regardless of scan size (issue #4,
    still unresolved as a root cause); since login is no longer required
    for this retailer, nothing is lost by not holding one browser context
    for an entire multi-store scan, so recycling periodically bounds the
    leak's damage instead of letting it compound across an entire run.
    None (the default) preserves the old behavior of one context for the
    whole scan.

    `on_progress`: called with a small dict at each checkpoint (stores
    found, store started, department started/product-count-known,
    per-item heartbeat, department done) -- feeds the dashboard's live
    status view (scanner/main.py's /status) so "what's it doing right
    now" doesn't require reading logs or the DB by hand (confirmed real
    friction this session, GitHub issue #2). Deliberately reuses the same
    checkpoints as the logging above rather than firing per-item.
    """

    def _progress(**fields) -> None:
        if on_progress is not None:
            on_progress(fields)

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
    if store_ids:
        stores = [s for s in stores if s.retailer_store_id in store_ids]
    # Progress logging below is deliberately checkpoint-based (store,
    # department, and a periodic heartbeat during price checks), not
    # per-item -- confirmed live 2026-08-31 the orchestrator previously
    # logged nothing at all on the success path, only on failures, which
    # left the dashboard's Logs tab empty for the entire length of a scan.
    # Per-item logging would also blow past the scanner's 500-line ring
    # buffer (scanner/log_buffer.py) well before a real scan finishes.
    logger.info("%s: found %d store(s) within %s miles of %s", adapter.retailer_slug, len(stores), radius_miles, zip_code)
    _progress(phase="stores", stores_total=len(stores))

    # Departments aren't store-specific (a retailer's category structure is
    # the same everywhere), so this only needs to happen once per scan, not
    # once per store -- confirmed live 2026-09-01 the old code re-ran a
    # multi-page sitemap crawl once per store (14x redundant work for a
    # 14-store scan) for identical results every time.
    scan_run_id = db.start_scan_run(conn, retailer_id, None, "departments", trigger)
    all_departments = list(adapter.discover_departments(browser_ctx))
    departments = _select_departments(all_departments, watched_departments, department_filter)
    db.finish_scan_run(conn, scan_run_id, "completed", 0, 0)
    logger.info(
        "%d department(s) discovered, %d match the watch list",
        len(all_departments), len(departments),
    )
    _progress(phase="departments", departments_total=len(departments))
    department_ids = {
        department.retailer_department_id: db.upsert_department(
            conn, retailer_id, department.retailer_department_id, department.name,
            parent_department_id=None,  # resolved lazily; parent linkage is a nice-to-have, not load-bearing
        )
        for department in departments
    }

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
            store_info.name, store_info.address, store_info.distance_miles,
        )
        stores_scanned += 1
        store_departments_scanned = 0
        logger.info("Store %s (%s): scanning", store_info.name or store_info.retailer_store_id, store_info.retailer_store_id)
        _progress(
            phase="store", store=store_info.name or store_info.retailer_store_id,
            store_index=stores_scanned, stores_total=len(stores),
            products_checked=products_checked, errors_count=errors_count,
        )

        for department in departments:
            department_id = department_ids[department.retailer_department_id]
            departments_scanned += 1
            store_departments_scanned += 1

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
            _progress(
                phase="prices", department=department.name,
                department_index=store_departments_scanned, departments_total=len(departments),
                department_products_total=len(product_refs), department_products_checked=0,
                products_checked=products_checked, errors_count=errors_count,
            )

            scan_run_id = db.start_scan_run(conn, retailer_id, store_id, "prices", trigger)
            department_hits = 0
            department_errors = 0
            # check_prices() (not the old per-item check_price() loop) --
            # the default implementation on RetailerAdapter behaves
            # identically to the old loop, but HomeDepotAdapter overrides
            # it with real wave-based batched+concurrent requests (modeled
            # on HDScanner's own validated approach: ~90x fewer round
            # trips for the same work). Pacing between batches/waves is
            # now the adapter's own concern -- `limiter` is still passed
            # through so 403/success events keep landing in the same
            # rate_limit_event log regardless of which implementation runs.
            for i, result in enumerate(
                adapter.check_prices(browser_ctx, product_refs, store_info, limiter), start=1
            ):
                if result.error:
                    logger.error("Failed checking price for %s: %s", result.product_ref.retailer_product_id, result.error)
                    errors_count += 1
                    department_errors += 1
                    continue

                observation = result.observation
                product_ref = result.product_ref

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

                # Heartbeat every 10 results so a long department still
                # shows live progress -- for a batching adapter these can
                # arrive in bursts (one per wave) rather than a steady
                # drip, which is fine, just less evenly spaced.
                if i % 10 == 0 and i != len(product_refs):
                    logger.info("Department %r: checked %d/%d so far", department.name, i, len(product_refs))
                    _progress(
                        phase="prices", department=department.name,
                        department_index=store_departments_scanned, departments_total=len(departments),
                        department_products_total=len(product_refs), department_products_checked=i,
                        products_checked=products_checked, errors_count=errors_count,
                    )

            db.finish_scan_run(conn, scan_run_id, "completed", products_checked, errors_count)
            logger.info(
                "Department %r: done -- %d checked, %d hit(s), %d error(s)",
                department.name, len(product_refs), department_hits, department_errors,
            )
            _progress(
                phase="prices", department=department.name,
                department_index=store_departments_scanned, departments_total=len(departments),
                department_products_total=len(product_refs), department_products_checked=len(product_refs),
                products_checked=products_checked, errors_count=errors_count,
            )

        if recycle_browser_ctx is not None:
            browser_ctx = recycle_browser_ctx(browser_ctx)

    # "Not yet" deals whose threshold a fresh observation just satisfied --
    # at any of the product's stores, not just the one it was deferred at
    # (see db.reactivate_satisfied_defers's docstring). One pass at the end
    # of the scan rather than threaded into the per-item check loop above.
    reactivated_count = db.reactivate_satisfied_defers(conn)
    if reactivated_count:
        logger.info("%d deferred deal(s) reactivated (threshold met)", reactivated_count)

    return {
        "stores_scanned": stores_scanned,
        "departments_scanned": departments_scanned,
        "products_checked": products_checked,
        "errors_count": errors_count,
        "new_deal_product_ids": new_deal_ids,
        "browser_ctx": browser_ctx,
    }


def repair_missing_enrichment(
    conn, browser_ctx, adapter: RetailerAdapter, limit: int | None = None,
) -> dict:
    """Backfills image_url/canonical_url/aisle/bay for deals whose product
    is missing them -- independent of current clearance/penny status,
    unlike check_prices()'s enrichment (only a confirmed hit gets
    enriched, deliberately, to avoid an API call per product checked).
    Two real cases this recovers from: a deal that was only ever a hit
    before enrichment existed at all, and a deal whose store has since
    fallen outside the configured ZIP/radius, so a normal scan never
    reaches it again to re-enrich it as a fresh hit.

    Grouped by store so enrich_batch() gets one batched call per store
    instead of one round trip per product. `limit` bounds how many deals
    (across all stores) get attempted in one run -- unset (None) means
    "quite possibly a lot," so the caller should default to something
    conservative for an on-demand tool talking to a real retailer API.
    """
    targets = db.get_deals_missing_enrichment(conn, limit=limit)

    by_store: dict[str, list[dict]] = {}
    for row in targets:
        by_store.setdefault(row["retailer_store_id"], []).append(row)

    attempted = 0
    images_filled = 0
    canonical_filled = 0
    aisle_bay_filled = 0
    errors = 0

    for retailer_store_id, rows in by_store.items():
        store = StoreInfo(
            retailer_store_id=retailer_store_id, zip_code=rows[0]["zip_code"],
            name=rows[0]["store_name"], address=rows[0]["store_address"],
        )
        # department is required by ProductRef but unused by enrich_batch
        # (only .retailer_product_id is) -- a placeholder avoids a real
        # department lookup this call has no other use for.
        placeholder_department = Department(retailer_department_id="", name="")
        product_refs = [
            ProductRef(
                retailer_product_id=row["retailer_product_id"], name=row["product_name"],
                department=placeholder_department,
            )
            for row in rows
        ]

        try:
            results = adapter.enrich_batch(browser_ctx, store, product_refs)
        except Exception:
            logger.exception(
                "Repair: enrich_batch failed for store %s (%d product(s))",
                retailer_store_id, len(rows),
            )
            errors += len(rows)
            continue

        for row in rows:
            attempted += 1
            data = results.get(row["retailer_product_id"])
            if not data:
                errors += 1
                continue
            if data.get("image_url"):
                images_filled += 1
            if data.get("canonical_url"):
                canonical_filled += 1
            db.repair_product_enrichment(
                conn, row["product_id"], data.get("canonical_url"), data.get("image_url"),
            )
            if data.get("aisle") or data.get("bay"):
                aisle_bay_filled += 1
            db.repair_store_product_location(
                conn, row["product_id"], row["store_id"], data.get("aisle"), data.get("bay"),
            )

    return {
        "attempted": attempted,
        "images_filled": images_filled,
        "canonical_filled": canonical_filled,
        "aisle_bay_filled": aisle_bay_filled,
        "errors": errors,
    }


def refresh_single_product(conn, browser_ctx, adapter: RetailerAdapter, product_id: int) -> dict:
    """On-demand "check this one item right now, everywhere" -- confirmed
    live 2026-09-01: the normal per-department scan cadence means a
    product a user is actively looking at can sit unchecked (or its
    inventory data stale/missing) for a while. Re-checks every store
    currently on record for the product's retailer, one at a time,
    reusing check_price() (the existing single-item path, which already
    does its own enrichment on a hit) rather than check_prices()'s
    wave-batching -- this is one product at N stores, not N products at
    one store, so there's no batching win to have.

    Called from scanner/main.py's refresh queue, never directly from a
    web request -- the queue is what lets someone "mash the button"
    across many products without racing requests against the same
    browser_ctx or hammering the retailer concurrently.
    """
    row = conn.execute(
        "SELECT retailer_id, retailer_product_id, name, department_id FROM product WHERE id = %s",
        (product_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"refresh_single_product: no product with id {product_id}")

    stores = conn.execute(
        "SELECT id, retailer_store_id, zip_code, name, address FROM store WHERE retailer_id = %s",
        (row["retailer_id"],),
    ).fetchall()

    # department is required by ProductRef but unused by check_price
    # beyond passing it back on the ProductRef it's given -- same
    # placeholder pattern as repair_missing_enrichment above.
    placeholder_department = Department(retailer_department_id="", name="")
    product_ref = ProductRef(
        retailer_product_id=row["retailer_product_id"], name=row["name"], department=placeholder_department,
    )

    checked = 0
    hits = 0
    errors = 0

    for store_row in stores:
        store = StoreInfo(
            retailer_store_id=store_row["retailer_store_id"], zip_code=store_row["zip_code"],
            name=store_row["name"], address=store_row["address"],
        )
        try:
            adapter.select_store(browser_ctx, store)
            observation = adapter.check_price(browser_ctx, product_ref, store)
        except Exception:
            logger.exception(
                "Refresh: check_price failed for product %s at store %s",
                product_id, store_row["retailer_store_id"],
            )
            errors += 1
            continue

        checked += 1
        db.upsert_product(
            conn, row["retailer_id"], product_ref.retailer_product_id, product_ref.name,
            department_id=row["department_id"], upc=None,
            image_url=observation.image_url, canonical_url=observation.canonical_url,
        )
        db.upsert_store_product_location(conn, product_id, store_row["id"], observation.aisle, observation.bay)
        observation_id = db.insert_price_observation(
            conn, product_id, store_row["id"], None, observation.observed_at,
            observation.price_cents, observation.list_price_cents,
            observation.is_clearance, observation.is_penny,
            observation.fulfillment_state, observation.stock_quantity, observation.raw_signal,
        )
        db.upsert_deal_from_observation(
            conn, product_id, store_row["id"], observation_id, observation.is_clearance, observation.is_penny,
        )
        if observation.is_clearance or observation.is_penny:
            hits += 1

    return {"stores_total": len(stores), "checked": checked, "hits": hits, "errors": errors}
