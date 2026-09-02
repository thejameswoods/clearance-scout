"""Regression test for the retailer display-name bug found during the
design-v2 gap close: orchestrator.py used to pass adapter.retailer_slug as
BOTH the slug and the display_name to db.upsert_retailer, so the raw slug
("home_depot") ended up in the `retailer.display_name` column and leaked
into any UI that reads it -- the sidebar tree, the scope bar, and the new
header phase breadcrumb (wireframe 5b). Fixed by giving adapters their own
retailer_display_name attribute and threading it through both
db.upsert_retailer call sites in orchestrator.py."""

from __future__ import annotations

from adapters.base import Department, ProductRef, StoreInfo
from scanner.orchestrator import rescan_stores, run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _adapter():
    store = StoreInfo(retailer_store_id="store-1", zip_code="00000", name="Store 1")
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {"dept-1": [ProductRef(retailer_product_id="sku-1", name="Widget", department=dept)]}
    return ConfigurableFakeAdapter(stores=[store], departments=[dept], products_by_department=products)


def test_run_scan_upserts_the_adapters_display_name_not_its_slug(postgres_conn):
    adapter = _adapter()
    assert adapter.retailer_slug != adapter.retailer_display_name  # otherwise this test can't catch a regression

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    row = postgres_conn.execute(
        "SELECT slug, display_name FROM retailer WHERE slug = %s", (adapter.retailer_slug,)
    ).fetchone()
    assert row["slug"] == adapter.retailer_slug
    assert row["display_name"] == adapter.retailer_display_name


def test_rescan_stores_upserts_the_adapters_display_name_not_its_slug(postgres_conn):
    adapter = _adapter()

    rescan_stores(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    row = postgres_conn.execute(
        "SELECT display_name FROM retailer WHERE slug = %s", (adapter.retailer_slug,)
    ).fetchone()
    assert row["display_name"] == adapter.retailer_display_name


def test_a_pre_existing_slug_named_row_self_corrects_on_next_scan(postgres_conn):
    """Existing rows in a live deployment currently hold the slug as their
    display_name (the bug this fixes) -- upsert_retailer's ON CONFLICT
    already updates display_name, so the very next scan repairs them with
    no migration needed."""
    from common import db

    adapter = _adapter()
    db.upsert_retailer(postgres_conn, adapter.retailer_slug, adapter.retailer_slug, "")
    row = postgres_conn.execute(
        "SELECT display_name FROM retailer WHERE slug = %s", (adapter.retailer_slug,)
    ).fetchone()
    assert row["display_name"] == adapter.retailer_slug  # the pre-fix state

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    row = postgres_conn.execute(
        "SELECT display_name FROM retailer WHERE slug = %s", (adapter.retailer_slug,)
    ).fetchone()
    assert row["display_name"] == adapter.retailer_display_name
