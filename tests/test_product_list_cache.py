"""Phase 2 (product-ID discovery) caching -- confirmed live 2026-08-31 that
re-listing every department's products on every single scan was pure
waste (products rarely change; current price/clearance always needs a
fresh check regardless). See orchestrator.py's run_scan docstring and
common/db.py's get_department_products_last_listed_at /
mark_department_products_listed / list_cached_products_for_department.
"""

from __future__ import annotations

from adapters.base import Department, ProductRef
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _widget_adapter():
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {
        "dept-1": [ProductRef(retailer_product_id="sku-1", name="Test Widget", department=dept)]
    }
    return ConfigurableFakeAdapter(departments=[dept], products_by_department=products)


def test_second_scan_within_cache_window_skips_list_products(postgres_conn):
    adapter = _widget_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", product_list_cache_hours=24)
    assert adapter.list_products_call_count == 1

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", product_list_cache_hours=24)
    assert adapter.list_products_call_count == 1  # still 1 -- served from cache


def test_cached_scan_still_checks_price_and_reports_the_product(postgres_conn):
    adapter = _widget_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", product_list_cache_hours=24)
    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", product_list_cache_hours=24)

    assert result["products_checked"] == 1  # price still checked fresh from cached product list


def test_zero_cache_hours_always_relists(postgres_conn):
    adapter = _widget_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", product_list_cache_hours=0)
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", product_list_cache_hours=0)

    assert adapter.list_products_call_count == 2


def test_multiple_stores_in_one_run_share_the_cache_after_first_store(postgres_conn):
    # The same department gets discovered once per store within a single
    # scan (find_stores can return several) -- the cache should mean only
    # the first store's pass actually calls list_products.
    from adapters.base import StoreInfo

    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {"dept-1": [ProductRef(retailer_product_id="sku-1", name="Test Widget", department=dept)]}
    adapter = ConfigurableFakeAdapter(
        stores=[
            StoreInfo(retailer_store_id="store-1", zip_code="00000", name="Store 1"),
            StoreInfo(retailer_store_id="store-2", zip_code="00000", name="Store 2"),
        ],
        departments=[dept],
        products_by_department=products,
    )

    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", product_list_cache_hours=24)

    assert result["stores_scanned"] == 2
    assert adapter.list_products_call_count == 1
