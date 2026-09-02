"""Department/keyword watch-list filtering — the "I only care about a few
departments, and really only electrical wire within them" use case. These
narrow what the scanner *requests* in the first place (fewer departments
listed, fewer products price-checked), not just what the dashboard displays
afterward — smaller footprint against the retailer's site is also lower
detection risk.
"""

from __future__ import annotations

from adapters.base import Department, ProductRef
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
