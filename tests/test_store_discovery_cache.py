"""Store-discovery caching -- find_stores() was called fresh,
unconditionally, on every single scan, same gap as department discovery
before it (see tests/test_department_discovery_cache.py). See
scanner/orchestrator.py's run_scan docstring and common/db.py's
get_stores_last_discovered_at / mark_stores_discovered /
list_stores_for_retailer.
"""

from __future__ import annotations

from adapters.base import Department, ProductRef, StoreInfo
from common import db
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _widget_adapter(stores=None):
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {
        "dept-1": [ProductRef(retailer_product_id="sku-1", name="Test Widget", department=dept)]
    }
    kwargs = {"departments": [dept], "products_by_department": products}
    if stores is not None:
        kwargs["stores"] = stores
    return ConfigurableFakeAdapter(**kwargs)


def test_second_scan_within_cache_window_skips_find_stores(postgres_conn):
    adapter = _widget_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=24)
    assert adapter.find_stores_call_count == 1

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=24)
    assert adapter.find_stores_call_count == 1  # still 1 -- served from cache


def test_zero_cache_hours_always_rediscovers_stores(postgres_conn):
    adapter = _widget_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=0)
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=0)

    assert adapter.find_stores_call_count == 2


def test_cache_hit_still_finds_the_store_and_checks_prices(postgres_conn):
    adapter = _widget_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=24)
    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=24)

    assert result["stores_scanned"] == 1
    assert result["products_checked"] == 1


def test_cache_hit_still_respects_disabled_store_exclusion(postgres_conn):
    store_a = StoreInfo(retailer_store_id="store-a", zip_code="00000", name="Store A")
    store_b = StoreInfo(retailer_store_id="store-b", zip_code="00000", name="Store B")
    adapter = _widget_adapter(stores=[store_a, store_b])

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=24)
    disabled_store_id = postgres_conn.execute(
        "SELECT id FROM store WHERE retailer_store_id = 'store-b'"
    ).fetchone()["id"]
    db.set_store_enabled(postgres_conn, disabled_store_id, False)

    # Second scan is a cache hit (find_stores not called again) -- the
    # disabled store must still be excluded from scan scope.
    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=24)

    assert adapter.find_stores_call_count == 1
    assert result["stores_scanned"] == 1  # only store-a


def test_list_stores_for_retailer_includes_disabled_stores(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    store_id = db.upsert_store(postgres_conn, retailer_id, "store-a", "00000", "Store A", None)
    db.set_store_enabled(postgres_conn, store_id, False)

    rows = db.list_stores_for_retailer(postgres_conn, retailer_id)

    assert [r["retailer_store_id"] for r in rows] == ["store-a"]


def test_mark_and_get_stores_last_discovered_at(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    assert db.get_stores_last_discovered_at(postgres_conn, retailer_id) is None

    db.mark_stores_discovered(postgres_conn, retailer_id)

    assert db.get_stores_last_discovered_at(postgres_conn, retailer_id) is not None


def test_cache_hit_does_not_change_store_data(postgres_conn):
    adapter = _widget_adapter(stores=[StoreInfo(retailer_store_id="store-a", zip_code="00000", name="Store A", distance_miles=3.5)])

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=24)
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", store_discovery_cache_hours=24)

    row = postgres_conn.execute("SELECT distance_miles FROM store WHERE retailer_store_id = 'store-a'").fetchone()
    assert row["distance_miles"] == 3.5
