"""Department-discovery caching -- confirmed live 2026-09-07 that
re-crawling Home Depot's sitemaps fully on every scan cost ~27.65s/scan
average for an essentially-static department tree. See
scanner/orchestrator.py's run_scan docstring and common/db.py's
get_departments_last_discovered_at / mark_departments_discovered /
list_departments_for_retailer. Mirrors tests/test_product_list_cache.py's
shape for the analogous phase-2 cache.
"""

from __future__ import annotations

from adapters.base import Department, ProductRef
from common import db
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _widget_adapter():
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {
        "dept-1": [ProductRef(retailer_product_id="sku-1", name="Test Widget", department=dept)]
    }
    return ConfigurableFakeAdapter(departments=[dept], products_by_department=products)


def test_second_scan_within_cache_window_skips_discover_departments(postgres_conn):
    adapter = _widget_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", department_discovery_cache_hours=24)
    assert adapter.discover_departments_call_count == 1

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", department_discovery_cache_hours=24)
    assert adapter.discover_departments_call_count == 1  # still 1 -- served from cache


def test_zero_cache_hours_always_rediscovers(postgres_conn):
    adapter = _widget_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", department_discovery_cache_hours=0)
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", department_discovery_cache_hours=0)

    assert adapter.discover_departments_call_count == 2


def test_cached_scan_still_finds_the_right_departments_and_checks_prices(postgres_conn):
    adapter = _widget_adapter()

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", department_discovery_cache_hours=24)
    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", department_discovery_cache_hours=24)

    assert result["products_checked"] == 1
    names = {r["name"] for r in postgres_conn.execute("SELECT name FROM department").fetchall()}
    assert names == {"Widgets"}


def test_cached_scan_still_respects_an_explicit_department_filter(postgres_conn):
    # Proves department_filter/_select_departments still narrows correctly
    # against DB-reconstructed Department objects on a cache hit, not just
    # freshly-discovered ones.
    electrical = Department(retailer_department_id="dept-electrical", name="Electrical")
    plumbing = Department(retailer_department_id="dept-plumbing", name="Plumbing")
    products = {
        "dept-electrical": [ProductRef(retailer_product_id="sku-wire", name="Wire", department=electrical)],
        "dept-plumbing": [ProductRef(retailer_product_id="sku-pipe", name="Pipe", department=plumbing)],
    }
    adapter = ConfigurableFakeAdapter(departments=[electrical, plumbing], products_by_department=products)

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", department_discovery_cache_hours=24)
    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", department_discovery_cache_hours=24,
        department_filter="dept-plumbing",
    )

    assert adapter.discover_departments_call_count == 1  # second scan served from cache
    assert result["departments_scanned"] == 1
    row = postgres_conn.execute("SELECT name FROM product WHERE retailer_product_id = 'sku-pipe'").fetchone()
    assert row["name"] == "Pipe"


def test_list_departments_for_retailer_reads_back_upserted_rows(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    db.upsert_department(postgres_conn, retailer_id, "dept-1", "Widgets", None)

    rows = db.list_departments_for_retailer(postgres_conn, retailer_id)

    assert [(r["retailer_department_id"], r["name"]) for r in rows] == [("dept-1", "Widgets")]


def test_mark_and_get_departments_last_discovered_at(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    assert db.get_departments_last_discovered_at(postgres_conn, retailer_id) is None

    db.mark_departments_discovered(postgres_conn, retailer_id)

    assert db.get_departments_last_discovered_at(postgres_conn, retailer_id) is not None


def test_all_discovered_departments_are_upserted_even_if_unwatched(postgres_conn):
    # department_ids now upserts from all_departments (the full discovery
    # result), not just the watched subset -- needed so a later cache-hit
    # scan (or a change in watched_department_names) still sees the full
    # catalog, not just whatever happened to be watched last time.
    electrical = Department(retailer_department_id="dept-electrical", name="Electrical")
    plumbing = Department(retailer_department_id="dept-plumbing", name="Plumbing")
    adapter = ConfigurableFakeAdapter(departments=[electrical, plumbing], products_by_department={})

    run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        watched_department_names={"Electrical"},
    )

    names = {r["name"] for r in postgres_conn.execute("SELECT name FROM department").fetchall()}
    assert names == {"Electrical", "Plumbing"}
