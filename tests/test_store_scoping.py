"""Which discovered stores actually get price-checked -- an explicit
store_ids scope (e.g. the dashboard's "Scan Now" dialog) layered on top of
a standing Settings-disabled exclusion that always applies regardless of
that scope (see scanner/orchestrator.py:run_scan's store_ids docstring)."""

from __future__ import annotations

from adapters.base import Department, ProductRef, StoreInfo
from common import db
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _two_store_adapter():
    store_a = StoreInfo(retailer_store_id="store-a", zip_code="00000", name="Store A")
    store_b = StoreInfo(retailer_store_id="store-b", zip_code="00000", name="Store B")
    dept = Department(retailer_department_id="dept-1", name="Electrical")
    products = {"dept-1": [ProductRef(retailer_product_id="sku-wire", name="Wire", department=dept)]}
    return ConfigurableFakeAdapter(stores=[store_a, store_b], departments=[dept], products_by_department=products)


def test_explicit_store_ids_restricts_to_just_those_stores(postgres_conn):
    adapter = _two_store_adapter()

    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_ids=["store-a"])

    assert result["stores_scanned"] == 1
    row = postgres_conn.execute(
        "SELECT s.retailer_store_id FROM price_observation po JOIN store s ON s.id = po.store_id"
    ).fetchone()
    assert row["retailer_store_id"] == "store-a"


def test_a_store_left_out_of_store_ids_is_still_upserted(postgres_conn):
    """Distance/name/address for a store outside this run's scope should
    still stay current -- only its price-check phase is skipped."""
    adapter = _two_store_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_ids=["store-a"])

    row = postgres_conn.execute("SELECT name FROM store WHERE retailer_store_id = 'store-b'").fetchone()
    assert row is not None
    assert row["name"] == "Store B"


def test_disabled_store_is_never_scanned_even_when_explicitly_selected(postgres_conn):
    """The core layering rule: Settings-disabled is a standing exclusion
    that an explicit store_ids selection (e.g. Scan Now) can't override."""
    adapter = _two_store_adapter()
    retailer_id = db.upsert_retailer(postgres_conn, adapter.retailer_slug, adapter.retailer_slug, "")
    store_id = db.upsert_store(postgres_conn, retailer_id, "store-a", "00000", "Store A", None)
    db.set_store_enabled(postgres_conn, store_id, False)

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        store_ids=["store-a", "store-b"],  # explicitly includes the disabled store
    )

    assert result["stores_scanned"] == 1
    row = postgres_conn.execute(
        "SELECT s.retailer_store_id FROM price_observation po JOIN store s ON s.id = po.store_id"
    ).fetchone()
    assert row["retailer_store_id"] == "store-b"


def test_default_scope_excludes_disabled_stores(postgres_conn):
    adapter = _two_store_adapter()
    retailer_id = db.upsert_retailer(postgres_conn, adapter.retailer_slug, adapter.retailer_slug, "")
    store_id = db.upsert_store(postgres_conn, retailer_id, "store-a", "00000", "Store A", None)
    db.set_store_enabled(postgres_conn, store_id, False)

    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")  # no explicit store_ids

    assert result["stores_scanned"] == 1
    row = postgres_conn.execute(
        "SELECT s.retailer_store_id FROM price_observation po JOIN store s ON s.id = po.store_id"
    ).fetchone()
    assert row["retailer_store_id"] == "store-b"


def test_brand_new_store_is_scanned_by_default_not_treated_as_disabled(postgres_conn):
    """A store discovered for the first time has no `store` row yet at
    filter time -- it must default to included, not silently dropped just
    because there's nothing (yet) to check its `enabled` column against."""
    adapter = _two_store_adapter()

    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    assert result["stores_scanned"] == 2


def test_get_disabled_store_ids_direct(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    store_id = db.upsert_store(postgres_conn, retailer_id, "store-a", "00000", "Store A", None)
    db.upsert_store(postgres_conn, retailer_id, "store-b", "00000", "Store B", None)

    assert db.get_disabled_store_ids(postgres_conn, retailer_id) == set()

    db.set_store_enabled(postgres_conn, store_id, False)
    assert db.get_disabled_store_ids(postgres_conn, retailer_id) == {"store-a"}

    db.set_store_enabled(postgres_conn, store_id, True)
    assert db.get_disabled_store_ids(postgres_conn, retailer_id) == set()


def test_set_retailer_enabled(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    assert db.get_retailer_by_slug(postgres_conn, "fake_retailer")["enabled"] is True

    db.set_retailer_enabled(postgres_conn, retailer_id, False)
    assert db.get_retailer_by_slug(postgres_conn, "fake_retailer")["enabled"] is False


def test_a_never_scanned_retailer_slug_has_no_row(postgres_conn):
    assert db.get_retailer_by_slug(postgres_conn, "nonexistent_retailer") is None
