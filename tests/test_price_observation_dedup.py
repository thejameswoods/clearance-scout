"""common/db.py's record_price_observation -- confirmed live 2026-09-07 that
68% of all price_observation rows (360,805 of 530,649) were exact
duplicates of the immediately-prior check for the same product/store, pure
"nothing changed" noise from every scan unconditionally rechecking every
product at every store (that unconditional recheck is correct and stays --
this only changes whether an unchanged result gets ANOTHER row). See
common/db.py's record_price_observation/get_latest_price_observation
docstrings for the exact compared-tuple definition.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adapters.base import Department, ProductRef
from common import db
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


@pytest.fixture
def product_store(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    store_id = db.upsert_store(postgres_conn, retailer_id, "store-1", "00000", "Store 1", None)
    department_id = db.upsert_department(postgres_conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(postgres_conn, retailer_id, "sku-1", "Test Widget", department_id, None, None)
    return product_id, store_id


def _record(conn, product_id, store_id, *, price_cents=500, list_price_cents=1000,
            is_clearance=True, is_penny=False, fulfillment_state="in_stock",
            stock_quantity=7, raw_signal=None):
    return db.record_price_observation(
        conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents, list_price_cents, is_clearance, is_penny,
        fulfillment_state, stock_quantity, raw_signal if raw_signal is not None else {},
    )


def _row_count(conn, product_id, store_id):
    return conn.execute(
        "SELECT count(*) AS n FROM price_observation WHERE product_id = %s AND store_id = %s",
        (product_id, store_id),
    ).fetchone()["n"]


def test_first_observation_always_inserts(postgres_conn, product_store):
    product_id, store_id = product_store
    observation_id, was_inserted = _record(postgres_conn, product_id, store_id)

    assert was_inserted is True
    assert _row_count(postgres_conn, product_id, store_id) == 1


def test_identical_followup_observation_is_not_inserted(postgres_conn, product_store):
    product_id, store_id = product_store
    first_id, _ = _record(postgres_conn, product_id, store_id, price_cents=500)
    second_id, was_inserted = _record(postgres_conn, product_id, store_id, price_cents=500)

    assert was_inserted is False
    assert second_id == first_id
    assert _row_count(postgres_conn, product_id, store_id) == 1


@pytest.mark.parametrize("field,first,second", [
    ("price_cents", 500, 499),
    ("list_price_cents", 1000, 999),
    ("is_clearance", True, False),
    ("is_penny", False, True),
    ("fulfillment_state", "in_stock", "out_of_stock"),
    ("stock_quantity", 7, 6),
])
def test_a_changed_field_forces_a_new_row(postgres_conn, product_store, field, first, second):
    product_id, store_id = product_store
    base = {"price_cents": 500, "list_price_cents": 1000, "is_clearance": True,
            "is_penny": False, "fulfillment_state": "in_stock", "stock_quantity": 7}

    _record(postgres_conn, product_id, store_id, **{**base, field: first})
    _, was_inserted = _record(postgres_conn, product_id, store_id, **{**base, field: second})

    assert was_inserted is True
    assert _row_count(postgres_conn, product_id, store_id) == 2


def test_raw_signal_change_alone_does_not_force_a_new_row(postgres_conn, product_store):
    product_id, store_id = product_store
    _record(postgres_conn, product_id, store_id, raw_signal={"scan": "one"})
    _, was_inserted = _record(postgres_conn, product_id, store_id, raw_signal={"scan": "two", "extra": True})

    assert was_inserted is False
    assert _row_count(postgres_conn, product_id, store_id) == 1


def test_deal_latest_observation_id_stays_on_the_reused_row(postgres_conn, product_store):
    product_id, store_id = product_store
    first_id, _ = _record(postgres_conn, product_id, store_id)
    db.upsert_deal_from_observation(postgres_conn, product_id, store_id, first_id, True, False)
    first_checked_at = postgres_conn.execute(
        "SELECT last_checked_at, updated_at FROM deal WHERE product_id = %s AND store_id = %s",
        (product_id, store_id),
    ).fetchone()

    second_id, was_inserted = _record(postgres_conn, product_id, store_id)
    db.upsert_deal_from_observation(postgres_conn, product_id, store_id, second_id, True, False)
    deal = postgres_conn.execute(
        "SELECT latest_observation_id, last_checked_at, updated_at FROM deal WHERE product_id = %s AND store_id = %s",
        (product_id, store_id),
    ).fetchone()

    assert was_inserted is False
    assert deal["latest_observation_id"] == first_id  # still pinned to the original, reused row
    assert deal["last_checked_at"] >= first_checked_at["last_checked_at"]  # still advances every scan
    assert deal["updated_at"] >= first_checked_at["updated_at"]


def test_dedup_is_scoped_per_store(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    store_a = db.upsert_store(postgres_conn, retailer_id, "store-a", "00000", "Store A", None)
    store_b = db.upsert_store(postgres_conn, retailer_id, "store-b", "00000", "Store B", None)
    department_id = db.upsert_department(postgres_conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(postgres_conn, retailer_id, "sku-1", "Test Widget", department_id, None, None)

    _record(postgres_conn, product_id, store_a, price_cents=500)
    _, was_inserted = _record(postgres_conn, product_id, store_b, price_cents=500)

    # Same product, same price, but a DIFFERENT store -- store_b has no
    # prior observation of its own, so this must still insert.
    assert was_inserted is True
    assert _row_count(postgres_conn, product_id, store_a) == 1
    assert _row_count(postgres_conn, product_id, store_b) == 1


# --- orchestrator-level integration: a full run_scan, not just the DB call --

def test_two_scans_with_an_unchanged_price_write_one_observation_row(postgres_conn):
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {"dept-1": [ProductRef(retailer_product_id="sku-1", name="Test Widget", department=dept)]}
    adapter = ConfigurableFakeAdapter(departments=[dept], products_by_department=products, price_cents=500)

    first = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")
    second = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    # Freshness is unaffected -- every scan still genuinely re-checks.
    assert first["products_checked"] == 1
    assert second["products_checked"] == 1
    # Only storage is -- the second, identical check doesn't duplicate the row.
    row_count = postgres_conn.execute("SELECT count(*) AS n FROM price_observation").fetchone()["n"]
    assert row_count == 1


def test_two_scans_with_a_changed_price_write_two_observation_rows(postgres_conn):
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {"dept-1": [ProductRef(retailer_product_id="sku-1", name="Test Widget", department=dept)]}
    adapter = ConfigurableFakeAdapter(departments=[dept], products_by_department=products, price_cents=500)

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")
    adapter.price_cents = 400
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    row_count = postgres_conn.execute("SELECT count(*) AS n FROM price_observation").fetchone()["n"]
    assert row_count == 2
