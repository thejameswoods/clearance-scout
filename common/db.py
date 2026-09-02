"""Shared Postgres access, used by scanner/, web/backend/, and bot/.

Deliberately plain psycopg + hand-written SQL, no ORM — the schema is small
and stable (see db/init/001_schema.sql), and every caller already speaks in
the adapters/base.py dataclasses, so an ORM layer would just be another
translation step for no real benefit at this scale.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    # Read lazily, not at import time — importing this module (e.g.
    # transitively, via scanner.orchestrator, in a test that only needs the
    # orchestrator's pure logic) shouldn't require DATABASE_URL to be set.
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url, row_factory=dict_row, autocommit=True) as conn:
        yield conn


# --- retailer / store / department / product -------------------------------

def upsert_retailer(conn, slug: str, display_name: str, base_url: str, adapter_version: str = "0") -> int:
    row = conn.execute(
        """
        INSERT INTO retailer (slug, display_name, base_url, adapter_version)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (slug) DO UPDATE SET display_name = EXCLUDED.display_name,
            base_url = EXCLUDED.base_url, adapter_version = EXCLUDED.adapter_version
        RETURNING id
        """,
        (slug, display_name, base_url, adapter_version),
    ).fetchone()
    return row["id"]


def set_retailer_min_discount_pct(conn, retailer_id: int, min_discount_pct: float | None) -> None:
    """Settings tab's per-retailer minimum-discount floor -- None clears
    it back to "no floor". See list_deals/status_bar_counts for how it's
    applied (a default, overridden outright by an explicit request-level
    min_discount_pct filter, not stacked with it)."""
    conn.execute("UPDATE retailer SET min_discount_pct = %s WHERE id = %s", (min_discount_pct, retailer_id))


def upsert_store(conn, retailer_id: int, retailer_store_id: str, zip_code: str,
                  name: str | None, address: str | None, distance_miles: float | None = None) -> int:
    row = conn.execute(
        """
        INSERT INTO store (retailer_id, retailer_store_id, zip_code, name, address, distance_miles)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (retailer_id, retailer_store_id)
        DO UPDATE SET zip_code = EXCLUDED.zip_code, name = EXCLUDED.name, address = EXCLUDED.address,
                      distance_miles = EXCLUDED.distance_miles
        RETURNING id
        """,
        (retailer_id, retailer_store_id, zip_code, name, address, distance_miles),
    ).fetchone()
    return row["id"]


def upsert_department(conn, retailer_id: int, retailer_department_id: str, name: str,
                       parent_department_id: int | None) -> int:
    row = conn.execute(
        """
        INSERT INTO department (retailer_id, retailer_department_id, name, parent_department_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (retailer_id, retailer_department_id)
        DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        (retailer_id, retailer_department_id, name, parent_department_id),
    ).fetchone()
    return row["id"]


def get_department_products_last_listed_at(conn, department_id: int) -> datetime | None:
    row = conn.execute(
        "SELECT products_last_listed_at FROM department WHERE id = %s", (department_id,)
    ).fetchone()
    return row["products_last_listed_at"] if row else None


def mark_department_products_listed(conn, department_id: int) -> None:
    conn.execute(
        "UPDATE department SET products_last_listed_at = now() WHERE id = %s", (department_id,)
    )


def list_cached_products_for_department(conn, department_id: int) -> list[dict[str, Any]]:
    """The product-ID cache list_products() would otherwise re-fetch from
    the retailer's API on every scan -- see orchestrator.py's use of this
    alongside get_department_products_last_listed_at/
    mark_department_products_listed."""
    return conn.execute(
        "SELECT retailer_product_id, name, upc, image_url FROM product WHERE department_id = %s",
        (department_id,),
    ).fetchall()


def upsert_product(conn, retailer_id: int, retailer_product_id: str, name: str,
                    department_id: int | None, upc: str | None, image_url: str | None,
                    canonical_url: str | None = None) -> int:
    # image_url/canonical_url arrive as None on most calls (only known once
    # a clearance/penny hit triggers enrichment -- see
    # adapters/home_depot/adapter.py's _enrich_confirmed_hit) -- COALESCE
    # keeps whatever was already learned rather than overwriting it with
    # NULL on every subsequent plain price check of the same product.
    row = conn.execute(
        """
        INSERT INTO product (retailer_id, retailer_product_id, name, department_id, upc, image_url, canonical_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (retailer_id, retailer_product_id) DO UPDATE SET
            name = EXCLUDED.name, department_id = EXCLUDED.department_id, upc = EXCLUDED.upc,
            image_url = COALESCE(EXCLUDED.image_url, product.image_url),
            canonical_url = COALESCE(EXCLUDED.canonical_url, product.canonical_url),
            last_seen_at = now()
        RETURNING id
        """,
        (retailer_id, retailer_product_id, name, department_id, upc, image_url, canonical_url),
    ).fetchone()
    return row["id"]


def upsert_store_product_location(conn, product_id: int, store_id: int,
                                   aisle: str | None, bay: str | None) -> None:
    if aisle is None and bay is None:
        return
    conn.execute(
        """
        INSERT INTO store_product_location (product_id, store_id, aisle, bay, last_confirmed_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (product_id, store_id)
        DO UPDATE SET aisle = EXCLUDED.aisle, bay = EXCLUDED.bay, last_confirmed_at = now()
        """,
        (product_id, store_id, aisle, bay),
    )


# --- scan runs ---------------------------------------------------------------

def start_scan_run(conn, retailer_id: int, store_id: int | None, phase: str, trigger: str) -> int:
    row = conn.execute(
        """
        INSERT INTO scan_run (retailer_id, store_id, phase, status, triggered_by)
        VALUES (%s, %s, %s, 'running', %s)
        RETURNING id
        """,
        (retailer_id, store_id, phase, trigger),
    ).fetchone()
    return row["id"]


def finish_scan_run(conn, scan_run_id: int, status: str, products_checked: int, errors_count: int) -> None:
    conn.execute(
        """
        UPDATE scan_run SET status = %s, finished_at = now(),
            products_checked = %s, errors_count = %s
        WHERE id = %s
        """,
        (status, products_checked, errors_count, scan_run_id),
    )


# --- price observations / deals ---------------------------------------------

def insert_price_observation(conn, product_id: int, store_id: int, scan_run_id: int | None,
                              observed_at: datetime, price_cents: int, list_price_cents: int | None,
                              is_clearance: bool, is_penny: bool, fulfillment_state: str | None,
                              stock_quantity: int | None,
                              raw_signal: dict[str, Any]) -> int:
    import json

    row = conn.execute(
        """
        INSERT INTO price_observation
            (product_id, store_id, scan_run_id, observed_at, price_cents, list_price_cents,
             is_clearance, is_penny, fulfillment_state, stock_quantity, raw_signal)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (product_id, store_id, scan_run_id, observed_at, price_cents, list_price_cents,
         is_clearance, is_penny, fulfillment_state, stock_quantity, json.dumps(raw_signal)),
    ).fetchone()
    return row["id"]


def upsert_deal_from_observation(conn, product_id: int, store_id: int, observation_id: int,
                                  is_clearance: bool, is_penny: bool) -> tuple[int, bool]:
    """Refreshes (or creates) the `deal` read-model row for a product/store
    pair. Returns (deal_id, is_new) — is_new is what the bot uses to decide
    whether to alert."""
    existing = conn.execute(
        "SELECT id, status FROM deal WHERE product_id = %s AND store_id = %s",
        (product_id, store_id),
    ).fetchone()

    if not (is_clearance or is_penny):
        if existing and existing["status"] in ("new", "active"):
            conn.execute(
                "UPDATE deal SET status = 'stale', latest_observation_id = %s, updated_at = now() WHERE id = %s",
                (observation_id, existing["id"]),
            )
        return (existing["id"], False) if existing else (None, False)

    if existing:
        was_inactive = existing["status"] in ("stale",)
        conn.execute(
            """
            UPDATE deal SET latest_observation_id = %s, updated_at = now(),
                status = CASE WHEN status IN ('bought', 'dismissed', 'saved') THEN status
                              WHEN status = 'stale' THEN 'active' ELSE status END
            WHERE id = %s
            """,
            (observation_id, existing["id"]),
        )
        return existing["id"], was_inactive
    else:
        row = conn.execute(
            """
            INSERT INTO deal (product_id, store_id, first_observation_id, latest_observation_id, status)
            VALUES (%s, %s, %s, %s, 'new')
            RETURNING id
            """,
            (product_id, store_id, observation_id, observation_id),
        ).fetchone()
        return row["id"], True


# --- dispositions: dismiss (product-level) / defer ("not yet") --------------

def dismiss_product(conn, product_id: int) -> None:
    """"Not interested" — product-level and permanent, across every store
    (unlike deal.status, which is per product+store). product.dismissed_at
    is the authoritative flag list_deals checks (so a future deal row for
    this product at a store it hasn't been seen at yet still stays
    hidden); every EXISTING deal row also gets status='dismissed' so
    History (which still reads deal.status, unchanged by this feature)
    keeps showing it -- a dual-write, not a redundancy."""
    conn.execute("UPDATE product SET dismissed_at = now() WHERE id = %s", (product_id,))
    conn.execute(
        "UPDATE deal SET status = 'dismissed', updated_at = now() WHERE product_id = %s AND status != 'dismissed'",
        (product_id,),
    )


def undismiss_product(conn, product_id: int) -> None:
    """Undo for dismiss_product -- the Deals page's "undo" affordance.
    Existing deal rows go back to 'new' rather than whatever they were
    before (not tracked) -- reasonable for an immediate-undo action; a
    stale one will correct itself on the next scan regardless."""
    conn.execute("UPDATE product SET dismissed_at = NULL WHERE id = %s", (product_id,))
    conn.execute(
        "UPDATE deal SET status = 'new', updated_at = now() WHERE product_id = %s AND status = 'dismissed'",
        (product_id,),
    )


def defer_deal(conn, deal_id: int, defer_type: str, defer_value: float | None) -> None:
    """"Not yet" — removes this one product+store row from the active feed
    until defer_rule is satisfied (see reactivate_satisfied_defers, run
    once per scan)."""
    import json

    if defer_type not in ("discount_pct", "price", "penny"):
        raise ValueError(f"defer_deal: unknown defer_type {defer_type!r}")
    conn.execute(
        "UPDATE deal SET status = 'deferred', defer_rule = %s, updated_at = now() WHERE id = %s",
        (json.dumps({"type": defer_type, "value": defer_value}), deal_id),
    )


def undefer_deal(conn, deal_id: int) -> None:
    """The "Change" action on a deferred deal -- clears it back to 'new'
    without waiting for the rule to be satisfied."""
    conn.execute(
        "UPDATE deal SET status = 'new', defer_rule = NULL, updated_at = now() WHERE id = %s",
        (deal_id,),
    )


def reactivate_satisfied_defers(conn) -> int:
    """Run once at the end of every scan (scanner/orchestrator.py's
    run_scan). A deferred deal's threshold is evaluated against the most
    recent observation at EVERY store of that product, not just the store
    it was deferred at -- "Waiting for a deeper cut" on one store's price
    should still catch a different store hitting the threshold first (the
    design doc: "returns as new... at any store where it's met"). Scoped
    to only products that actually have a deferred deal, not a full
    price_observation scan."""
    rows = conn.execute(
        """
        WITH latest_per_store AS (
            SELECT DISTINCT ON (po.product_id, po.store_id)
                po.product_id, po.store_id, po.price_cents, po.list_price_cents, po.is_penny
            FROM price_observation po
            WHERE po.product_id IN (SELECT product_id FROM deal WHERE status = 'deferred')
            ORDER BY po.product_id, po.store_id, po.observed_at DESC
        )
        UPDATE deal d
        SET status = 'new', defer_rule = NULL, updated_at = now()
        WHERE d.status = 'deferred'
          AND d.defer_rule IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM latest_per_store lps
              WHERE lps.product_id = d.product_id
                AND (
                    (d.defer_rule->>'type' = 'penny' AND lps.is_penny)
                    OR (d.defer_rule->>'type' = 'price'
                        AND lps.price_cents <= (d.defer_rule->>'value')::numeric * 100)
                    OR (d.defer_rule->>'type' = 'discount_pct'
                        AND lps.list_price_cents > 0
                        AND (100.0 * (lps.list_price_cents - lps.price_cents) / lps.list_price_cents)
                            >= (d.defer_rule->>'value')::numeric)
                )
          )
        RETURNING d.id
        """
    ).fetchall()
    return len(rows)


def record_rate_limit_event(conn, retailer_id: int, event_type: str, detail: str | None) -> None:
    conn.execute(
        "INSERT INTO rate_limit_event (retailer_id, event_type, detail) VALUES (%s, %s, %s)",
        (retailer_id, event_type, detail),
    )


# --- data repair tools (Settings tab's "Data tools" section) ----------------

def recompute_deal_statuses(conn, override_manual: bool = False) -> int:
    """Re-derives each deal's status from its latest observation's
    is_clearance/is_penny -- the same reconciliation
    upsert_deal_from_observation does live during a scan, run on demand to
    repair drift (e.g. confirmed live 2026-09-01: a direct DB write outside
    the app set 63 deals to 'dismissed' with no corresponding app-level
    action in the web container's logs).

    override_manual=False (the default, safe to run anytime) leaves
    'bought'/'dismissed'/'saved' alone, same protection
    upsert_deal_from_observation gives them on a normal scan -- it only
    reconciles 'new'/'active'/'stale' drift. override_manual=True also
    rewrites those three; only for deliberate repair of data known to be
    wrong, since it can undo a real user action indistinguishably from an
    erroneous one.
    """
    protect_clause = "" if override_manual else "AND d.status NOT IN ('bought', 'dismissed', 'saved')"
    rows = conn.execute(
        f"""
        UPDATE deal d
        SET status = CASE WHEN po.is_clearance OR po.is_penny THEN 'active' ELSE 'stale' END,
            updated_at = now()
        FROM price_observation po
        WHERE po.id = d.latest_observation_id
          {protect_clause}
          AND d.status IS DISTINCT FROM
              (CASE WHEN po.is_clearance OR po.is_penny THEN 'active' ELSE 'stale' END)
        RETURNING d.id
        """
    ).fetchall()
    return len(rows)


def get_deals_missing_enrichment(conn, limit: int | None = None) -> list[dict[str, Any]]:
    """Deals whose product is missing image_url/canonical_url, or whose
    store_product_location is missing/incomplete -- the repair target for
    the "Repair missing data" tool. Scoped to actual deals (a confirmed
    clearance/penny hit at some point), not the whole product table --
    most products were never a hit and never had enrichment data to begin
    with, by design (adapter.py only enriches a confirmed hit). Ordered by
    the deal's own updated_at (most recently relevant first) so a limited
    run makes progress on what's most likely to still matter."""
    query = """
        SELECT DISTINCT p.id AS product_id, p.retailer_id, p.retailer_product_id,
               p.name AS product_name, s.id AS store_id, s.retailer_store_id,
               s.zip_code, s.name AS store_name, s.address AS store_address,
               dl.updated_at AS deal_updated_at
        FROM deal dl
        JOIN product p ON p.id = dl.product_id
        JOIN store s ON s.id = dl.store_id
        LEFT JOIN store_product_location spl ON spl.product_id = p.id AND spl.store_id = s.id
        WHERE p.image_url IS NULL OR p.canonical_url IS NULL
           OR spl.product_id IS NULL OR spl.aisle IS NULL OR spl.bay IS NULL
        ORDER BY dl.updated_at DESC
    """
    if limit is not None:
        return conn.execute(query + " LIMIT %s", (limit,)).fetchall()
    return conn.execute(query).fetchall()


def repair_product_enrichment(conn, product_id: int, canonical_url: str | None, image_url: str | None) -> None:
    """Fills image_url/canonical_url ONLY where currently NULL -- unlike
    upsert_product (which also rewrites name/department_id/upc on every
    call, correct for a live price-check where those are freshly known),
    a repair pass has no fresher name/department/upc to offer, so it must
    never touch them. COALESCE also means a repair attempt that comes back
    empty (e.g. the retailer genuinely has no image for this SKU) doesn't
    overwrite a good value learned from an earlier successful enrichment."""
    if canonical_url is None and image_url is None:
        return
    conn.execute(
        """
        UPDATE product SET
            image_url = COALESCE(product.image_url, %s),
            canonical_url = COALESCE(product.canonical_url, %s)
        WHERE id = %s
        """,
        (image_url, canonical_url, product_id),
    )


def repair_store_product_location(conn, product_id: int, store_id: int,
                                   aisle: str | None, bay: str | None) -> None:
    """The repair-safe counterpart to upsert_store_product_location:
    COALESCEs instead of overwriting, so a repair fetch that only recovers
    one of aisle/bay doesn't null out the other if it was already known."""
    if aisle is None and bay is None:
        return
    conn.execute(
        """
        INSERT INTO store_product_location (product_id, store_id, aisle, bay, last_confirmed_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (product_id, store_id) DO UPDATE SET
            aisle = COALESCE(store_product_location.aisle, EXCLUDED.aisle),
            bay = COALESCE(store_product_location.bay, EXCLUDED.bay),
            last_confirmed_at = now()
        """,
        (product_id, store_id, aisle, bay),
    )


def reset_department_product_cache(conn, retailer_slug: str | None = None) -> int:
    """Nulls products_last_listed_at so the next scan re-lists products for
    these departments from the retailer instead of serving the product-ID
    cache (scanner/orchestrator.py's cache_is_fresh gate) -- needed because
    that cache has no upper bound by default (product_list_cache_hours),
    so a department never rediscovers new products on its own. Confirmed
    live 2026-09-01: 266 products were invisible to the scanner this way
    until this same reset was run by hand via psql."""
    if retailer_slug:
        rows = conn.execute(
            """
            UPDATE department d SET products_last_listed_at = NULL
            FROM retailer r
            WHERE d.retailer_id = r.id AND r.slug = %s
            RETURNING d.id
            """,
            (retailer_slug,),
        ).fetchall()
    else:
        rows = conn.execute(
            "UPDATE department SET products_last_listed_at = NULL RETURNING id"
        ).fetchall()
    return len(rows)


def set_credential_session_status(conn, retailer_id: int, status: str, session_label: str = "default") -> None:
    conn.execute(
        """
        INSERT INTO credential_session (retailer_id, session_label, status, last_verified_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (retailer_id, session_label)
        DO UPDATE SET status = EXCLUDED.status, last_verified_at = now()
        """,
        (retailer_id, session_label, status),
    )


# --- editable scanner settings (dashboard overrides of env-var config) ------

SCANNER_SETTINGS_FIELDS = (
    "zip_code", "radius_miles", "watched_departments", "watch_keywords",
    "product_list_cache_hours",
)


def get_scanner_settings(conn) -> dict[str, Any] | None:
    """The single settings-override row, or None if nothing's ever been
    saved from the dashboard -- callers (scanner/main.py's
    _current_settings) fall back to env-var defaults for a None field
    within the row, and for a None row entirely."""
    return conn.execute(
        f"SELECT {', '.join(SCANNER_SETTINGS_FIELDS)} FROM scanner_settings WHERE id = 1"
    ).fetchone()


def upsert_scanner_settings(conn, **fields: Any) -> None:
    """Only the keys actually passed get written -- an omitted field
    leaves whatever's already stored (or NULL/"use the env default") for
    it untouched, rather than every save having to resend the full set."""
    unknown = set(fields) - set(SCANNER_SETTINGS_FIELDS)
    if unknown:
        raise ValueError(f"upsert_scanner_settings: unknown field(s) {unknown}")
    if not fields:
        return

    columns = list(fields.keys())
    conn.execute(
        f"""
        INSERT INTO scanner_settings (id, {", ".join(columns)}, updated_at)
        VALUES (1, {", ".join(["%s"] * len(columns))}, now())
        ON CONFLICT (id) DO UPDATE SET
            {", ".join(f"{c} = EXCLUDED.{c}" for c in columns)},
            updated_at = now()
        """,
        list(fields.values()),
    )
