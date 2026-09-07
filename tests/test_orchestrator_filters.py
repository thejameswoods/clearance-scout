"""Department/keyword watch-list filtering — the "I only care about a few
departments, and really only electrical wire within them" use case. These
narrow what the scanner *requests* in the first place (fewer departments
listed, fewer products price-checked), not just what the dashboard displays
afterward — smaller footprint against the retailer's site is also lower
detection risk.
"""

from __future__ import annotations

from adapters.base import Department, ProductRef, StoreInfo
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _electrical_and_plumbing_adapter():
    electrical = Department(retailer_department_id="dept-electrical", name="Electrical")
    plumbing = Department(retailer_department_id="dept-plumbing", name="Plumbing")
    products = {
        "dept-electrical": [
            ProductRef(retailer_product_id="sku-wire", name="12-Gauge THHN Wire", department=electrical),
            ProductRef(retailer_product_id="sku-outlet", name="Duplex Outlet", department=electrical),
        ],
        "dept-plumbing": [
            ProductRef(retailer_product_id="sku-pipe", name="PVC Pipe", department=plumbing),
        ],
    }
    return ConfigurableFakeAdapter(departments=[electrical, plumbing], products_by_department=products)


def test_watched_department_names_skips_unwatched_departments_entirely(postgres_conn):
    adapter = _electrical_and_plumbing_adapter()

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        watched_department_names={"Electrical"},
    )

    # Both products in Electrical checked; Plumbing's list_products should
    # never even be called.
    assert result["departments_scanned"] == 1
    assert result["products_checked"] == 2
    names = {r["name"] for r in postgres_conn.execute("SELECT name FROM product").fetchall()}
    assert names == {"12-Gauge THHN Wire", "Duplex Outlet"}


def test_watched_department_names_is_an_exact_match_not_a_substring(postgres_conn):
    # Explicit-selection semantics now (see common/db.py's
    # get_watched_department_names) -- unlike the old flat text field,
    # there's no partial/substring matching left at this layer. The set
    # passed in is already fully expanded (descendants included) by the
    # caller before it reaches here.
    adapter = _electrical_and_plumbing_adapter()

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        watched_department_names={"Electric"},  # not a real department name
    )

    assert result["departments_scanned"] == 0
    assert result["products_checked"] == 0


def test_watch_keywords_filters_products_within_watched_departments(postgres_conn):
    adapter = _electrical_and_plumbing_adapter()

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        watched_department_names={"Electrical"}, watch_keywords=["wire"],
    )

    assert result["products_checked"] == 1
    row = postgres_conn.execute("SELECT name FROM product").fetchone()
    assert row["name"] == "12-Gauge THHN Wire"


def test_no_watch_filters_scans_everything_by_default(postgres_conn):
    adapter = _electrical_and_plumbing_adapter()

    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    assert result["departments_scanned"] == 2
    assert result["products_checked"] == 3


def test_exclude_keywords_wins_over_a_matching_include(postgres_conn):
    """Issue #1's own example: Includes "String trimmer", Excludes
    "Refill" should alert on the trimmer but not on just its refills."""
    dept = Department(retailer_department_id="dept-1", name="Outdoor")
    products = {
        "dept-1": [
            ProductRef(retailer_product_id="sku-trimmer", name="String Trimmer 20V", department=dept),
            ProductRef(retailer_product_id="sku-refill", name="String Trimmer Refill Line", department=dept),
        ],
    }
    adapter = ConfigurableFakeAdapter(departments=[dept], products_by_department=products)

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        watch_keywords=["string trimmer"], exclude_keywords=["refill"],
    )

    assert result["products_checked"] == 1
    row = postgres_conn.execute("SELECT name FROM product").fetchone()
    assert row["name"] == "String Trimmer 20V"


def test_regex_mode_applies_to_both_include_and_exclude(postgres_conn):
    dept = Department(retailer_department_id="dept-1", name="Electrical")
    products = {
        "dept-1": [
            ProductRef(retailer_product_id="sku-1", name="12-Gauge Wire", department=dept),
            ProductRef(retailer_product_id="sku-2", name="10-Gauge Wire", department=dept),
            ProductRef(retailer_product_id="sku-3", name="Duplex Outlet", department=dept),
        ],
    }
    adapter = ConfigurableFakeAdapter(departments=[dept], products_by_department=products)

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        watch_keywords=[r"\d+-Gauge"], exclude_keywords=["^12"], keyword_filter_mode="regex",
    )

    assert result["products_checked"] == 1
    row = postgres_conn.execute("SELECT name FROM product").fetchone()
    assert row["name"] == "10-Gauge Wire"


def test_store_keyword_filter_replaces_global_for_that_store_only(postgres_conn):
    """A store-level override fully replaces the retailer-wide filter for
    that one store -- it doesn't layer with it (see
    db/init/001_schema.sql's store_keyword_filter docstring) -- and other
    stores keep using the retailer-wide filter unchanged."""
    store_a = StoreInfo(retailer_store_id="store-a", zip_code="00000", name="Store A")
    store_b = StoreInfo(retailer_store_id="store-b", zip_code="00000", name="Store B")
    dept = Department(retailer_department_id="dept-1", name="Outdoor")
    products = {
        "dept-1": [
            ProductRef(retailer_product_id="sku-trimmer", name="String Trimmer", department=dept),
            ProductRef(retailer_product_id="sku-mower", name="Push Mower", department=dept),
        ],
    }
    adapter = ConfigurableFakeAdapter(
        stores=[store_a, store_b], departments=[dept], products_by_department=products,
    )

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        watch_keywords=["trimmer"],  # global: only trimmers
        store_keyword_filters={
            "store-b": {"mode": "simple", "include_keywords": ["mower"], "exclude_keywords": None},
        },
    )

    assert result["products_checked"] == 2  # store-a: trimmer only, store-b: mower only
    rows = postgres_conn.execute(
        "SELECT s.retailer_store_id, p.name FROM price_observation po "
        "JOIN store s ON s.id = po.store_id JOIN product p ON p.id = po.product_id"
    ).fetchall()
    by_store = {r["retailer_store_id"]: r["name"] for r in rows}
    assert by_store == {"store-a": "String Trimmer", "store-b": "Push Mower"}


def test_department_filter_overrides_watch_list(postgres_conn):
    """A manual "scan just this one department" trigger (dashboard/bot
    /scan <department>) should work even for a department outside the
    configured watch list — it's an explicit override, not a suggestion."""
    adapter = _electrical_and_plumbing_adapter()

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        watched_department_names={"Electrical"},  # would normally exclude Plumbing
        department_filter="dept-plumbing",        # explicit manual override
    )

    assert result["departments_scanned"] == 1
    row = postgres_conn.execute("SELECT name FROM product").fetchone()
    assert row["name"] == "PVC Pipe"
