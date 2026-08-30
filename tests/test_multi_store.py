"""Radius-based multi-store scanning — "any store within N miles of me",
not just the single nearest store."""

from __future__ import annotations

from adapters.base import Department, ProductRef, StoreInfo
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def test_scans_every_store_the_adapter_finds_in_radius(postgres_conn):
    store_a = StoreInfo(retailer_store_id="store-a", zip_code="00000", name="Store A")
    store_b = StoreInfo(retailer_store_id="store-b", zip_code="00000", name="Store B")
    dept = Department(retailer_department_id="dept-1", name="Electrical")
    products = {"dept-1": [ProductRef(retailer_product_id="sku-wire", name="Wire", department=dept)]}
    adapter = ConfigurableFakeAdapter(stores=[store_a, store_b], departments=[dept], products_by_department=products)

    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", radius_miles=25)

    assert result["stores_scanned"] == 2
    assert result["products_checked"] == 2  # one product, checked at each of two stores

    store_ids = {r["store_id"] for r in postgres_conn.execute("SELECT DISTINCT store_id FROM price_observation").fetchall()}
    assert len(store_ids) == 2

    deals = postgres_conn.execute("SELECT store_id FROM deal ORDER BY store_id").fetchall()
    assert len(deals) == 2, "the same product on clearance at two different stores should be two separate deals"


def test_single_store_default_still_works(postgres_conn):
    """Backward-compatible default: an adapter/config that only ever finds
    one store behaves exactly like the original single-store flow."""
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    adapter = ConfigurableFakeAdapter(departments=[dept])

    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    assert result["stores_scanned"] == 1
