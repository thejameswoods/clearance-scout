"""orchestrator.rescan_stores -- Settings panel's "Rescan store list": just
re-runs store discovery and upserts each store, without touching
departments/products/prices (unlike a full run_scan)."""

from __future__ import annotations

from adapters.base import Department, StoreInfo
from scanner.orchestrator import rescan_stores
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def test_rescan_stores_upserts_every_discovered_store(postgres_conn):
    store_a = StoreInfo(retailer_store_id="store-a", zip_code="00000", name="Store A", distance_miles=3.5)
    store_b = StoreInfo(retailer_store_id="store-b", zip_code="00000", name="Store B", distance_miles=9.0)
    adapter = ConfigurableFakeAdapter(stores=[store_a, store_b])

    result = rescan_stores(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", radius_miles=25)

    assert result == {"stores_found": 2}
    rows = postgres_conn.execute("SELECT retailer_store_id, distance_miles FROM store ORDER BY retailer_store_id").fetchall()
    assert [(r["retailer_store_id"], r["distance_miles"]) for r in rows] == [("store-a", 3.5), ("store-b", 9.0)]


def test_rescan_stores_refreshes_distance_on_an_existing_store(postgres_conn):
    adapter_v1 = ConfigurableFakeAdapter(stores=[StoreInfo(retailer_store_id="store-a", zip_code="00000", distance_miles=10.0)])
    rescan_stores(postgres_conn, FakeBrowserContext(), adapter_v1, zip_code="00000")

    adapter_v2 = ConfigurableFakeAdapter(stores=[StoreInfo(retailer_store_id="store-a", zip_code="00000", distance_miles=4.2)])
    rescan_stores(postgres_conn, FakeBrowserContext(), adapter_v2, zip_code="00000")

    row = postgres_conn.execute("SELECT distance_miles FROM store WHERE retailer_store_id = 'store-a'").fetchone()
    assert row["distance_miles"] == 4.2


def test_rescan_stores_never_touches_departments_or_products(postgres_conn):
    dept = Department(retailer_department_id="dept-1", name="Electrical")
    adapter = ConfigurableFakeAdapter(stores=[StoreInfo(retailer_store_id="store-a", zip_code="00000")], departments=[dept])

    rescan_stores(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    assert adapter.discover_departments_call_count == 0
    assert adapter.list_products_call_count == 0
    assert postgres_conn.execute("SELECT count(*) AS n FROM department").fetchone()["n"] == 0
    assert postgres_conn.execute("SELECT count(*) AS n FROM product").fetchone()["n"] == 0


def test_rescan_stores_does_not_disturb_an_existing_enabled_flag(postgres_conn):
    from common import db

    adapter = ConfigurableFakeAdapter(stores=[StoreInfo(retailer_store_id="store-a", zip_code="00000")])
    rescan_stores(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")
    store_id = postgres_conn.execute("SELECT id FROM store WHERE retailer_store_id = 'store-a'").fetchone()["id"]
    db.set_store_enabled(postgres_conn, store_id, False)

    rescan_stores(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    row = postgres_conn.execute("SELECT enabled FROM store WHERE id = %s", (store_id,)).fetchone()
    assert row["enabled"] is False
