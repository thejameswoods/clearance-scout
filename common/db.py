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


def upsert_store(conn, retailer_id: int, retailer_store_id: str, zip_code: str,
                  name: str | None, address: str | None) -> int:
    row = conn.execute(
        """
        INSERT INTO store (retailer_id, retailer_store_id, zip_code, name, address)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (retailer_id, retailer_store_id)
        DO UPDATE SET zip_code = EXCLUDED.zip_code, name = EXCLUDED.name, address = EXCLUDED.address
        RETURNING id
        """,
        (retailer_id, retailer_store_id, zip_code, name, address),
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
                    department_id: int | None, upc: str | None, image_url: str | None) -> int:
    row = conn.execute(
        """
        INSERT INTO product (retailer_id, retailer_product_id, name, department_id, upc, image_url)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (retailer_id, retailer_product_id) DO UPDATE SET
            name = EXCLUDED.name, department_id = EXCLUDED.department_id,
            upc = EXCLUDED.upc, image_url = EXCLUDED.image_url, last_seen_at = now()
        RETURNING id
        """,
        (retailer_id, retailer_product_id, name, department_id, upc, image_url),
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


def record_rate_limit_event(conn, retailer_id: int, event_type: str, detail: str | None) -> None:
    conn.execute(
        "INSERT INTO rate_limit_event (retailer_id, event_type, detail) VALUES (%s, %s, %s)",
        (retailer_id, event_type, detail),
    )


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
