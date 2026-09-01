"""Settings tab's "Data tools" section (web/backend/routes/admin.py) --
recompute_deal_statuses and reset_department_product_cache in common/db.py.
Built to repair drift without SSHing in and hand-writing SQL, confirmed
live 2026-09-01 for two real incidents: an unbounded product-list cache
that hid 266 products from the scanner, and 63 deals silently flipped to
'dismissed' by a direct DB write that never went through the app."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from common import db

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:test@localhost:5433/postgres"
)


def _make_deal(conn, *, is_clearance: bool) -> int:
    """A minimal retailer/store/department/product/observation/deal chain,
    returning the deal id. Mirrors what orchestrator.run_scan() does for
    a single confirmed hit, without needing a fake browser/adapter."""
    retailer_id = db.upsert_retailer(conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    store_id = db.upsert_store(conn, retailer_id, "store-1", "00000", "Fake Store", None)
    department_id = db.upsert_department(conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(conn, retailer_id, "sku-1", "Test Widget", department_id, None, None)

    observation_id = db.insert_price_observation(
        conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=999, list_price_cents=1999,
        is_clearance=is_clearance, is_penny=False,
        fulfillment_state="in_stock", stock_quantity=5, raw_signal={},
    )
    deal_id, _ = db.upsert_deal_from_observation(
        conn, product_id, store_id, observation_id, is_clearance=is_clearance, is_penny=False,
    )
    return deal_id


def _status(conn, deal_id: int) -> str:
    return conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()["status"]


# --- recompute_deal_statuses --------------------------------------------

def test_recompute_leaves_dismissed_alone_by_default(postgres_conn):
    deal_id = _make_deal(postgres_conn, is_clearance=True)
    from web.backend import queries
    queries.set_deal_status(postgres_conn, deal_id, "dismissed")

    updated = db.recompute_deal_statuses(postgres_conn, override_manual=False)

    assert updated == 0
    assert _status(postgres_conn, deal_id) == "dismissed"


def test_recompute_with_override_restores_a_still_live_deal(postgres_conn):
    """The exact repair for the 2026-09-01 incident: a deal wrongly marked
    'dismissed' whose latest observation is still a real clearance hit."""
    deal_id = _make_deal(postgres_conn, is_clearance=True)
    from web.backend import queries
    queries.set_deal_status(postgres_conn, deal_id, "dismissed")

    updated = db.recompute_deal_statuses(postgres_conn, override_manual=True)

    assert updated == 1
    assert _status(postgres_conn, deal_id) == "active"


def test_recompute_with_override_does_not_resurrect_a_dead_deal(postgres_conn):
    """override_manual doesn't mean "make everything active" -- a deal
    whose latest observation no longer shows clearance/penny goes to
    'stale', not 'active', even though it's being force-recomputed."""
    deal_id = _make_deal(postgres_conn, is_clearance=True)
    product_row = postgres_conn.execute(
        "SELECT product_id, store_id FROM deal WHERE id = %s", (deal_id,)
    ).fetchone()
    # A second, non-clearance observation -- upsert_deal_from_observation
    # naturally moves the deal to 'stale' and repoints latest_observation_id.
    stale_observation_id = db.insert_price_observation(
        postgres_conn, product_row["product_id"], product_row["store_id"], None,
        datetime.now(timezone.utc), price_cents=1999, list_price_cents=None,
        is_clearance=False, is_penny=False, fulfillment_state="in_stock",
        stock_quantity=5, raw_signal={},
    )
    db.upsert_deal_from_observation(
        postgres_conn, product_row["product_id"], product_row["store_id"],
        stale_observation_id, is_clearance=False, is_penny=False,
    )
    from web.backend import queries
    queries.set_deal_status(postgres_conn, deal_id, "dismissed")  # simulate the bad write

    updated = db.recompute_deal_statuses(postgres_conn, override_manual=True)

    assert updated == 1
    assert _status(postgres_conn, deal_id) == "stale"


def test_recompute_does_not_count_or_touch_already_correct_deals(postgres_conn):
    """Confirmed live 2026-09-01: running the tool a second time with
    nothing actually wrong reported "updated: 74" for a table that hadn't
    changed -- the UPDATE matched every non-protected row unconditionally
    and counted all of them, not just the ones whose status changed."""
    deal_id = _make_deal(postgres_conn, is_clearance=True)
    postgres_conn.execute("UPDATE deal SET status = 'active' WHERE id = %s", (deal_id,))
    before = postgres_conn.execute(
        "SELECT status, updated_at FROM deal WHERE id = %s", (deal_id,)
    ).fetchone()

    updated = db.recompute_deal_statuses(postgres_conn, override_manual=False)

    assert updated == 0
    after = postgres_conn.execute(
        "SELECT status, updated_at FROM deal WHERE id = %s", (deal_id,)
    ).fetchone()
    assert after["updated_at"] == before["updated_at"]  # not touched


def test_recompute_without_override_still_reconciles_new_and_active(postgres_conn):
    deal_id = _make_deal(postgres_conn, is_clearance=True)
    postgres_conn.execute("UPDATE deal SET status = 'stale' WHERE id = %s", (deal_id,))

    updated = db.recompute_deal_statuses(postgres_conn, override_manual=False)

    assert updated == 1
    assert _status(postgres_conn, deal_id) == "active"


# --- reset_department_product_cache -------------------------------------

def test_reset_department_cache_nulls_all_by_default(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    dept_id = db.upsert_department(postgres_conn, retailer_id, "dept-1", "Widgets", None)
    db.mark_department_products_listed(postgres_conn, dept_id)
    assert db.get_department_products_last_listed_at(postgres_conn, dept_id) is not None

    reset_count = db.reset_department_product_cache(postgres_conn)

    assert reset_count == 1
    assert db.get_department_products_last_listed_at(postgres_conn, dept_id) is None


def test_reset_department_cache_scoped_to_one_retailer(postgres_conn):
    r1 = db.upsert_retailer(postgres_conn, "retailer_one", "Retailer One", "https://example.invalid")
    r2 = db.upsert_retailer(postgres_conn, "retailer_two", "Retailer Two", "https://example.invalid")
    d1 = db.upsert_department(postgres_conn, r1, "dept-1", "Widgets", None)
    d2 = db.upsert_department(postgres_conn, r2, "dept-1", "Gadgets", None)
    db.mark_department_products_listed(postgres_conn, d1)
    db.mark_department_products_listed(postgres_conn, d2)

    reset_count = db.reset_department_product_cache(postgres_conn, retailer_slug="retailer_one")

    assert reset_count == 1
    assert db.get_department_products_last_listed_at(postgres_conn, d1) is None
    assert db.get_department_products_last_listed_at(postgres_conn, d2) is not None


# --- routes ---------------------------------------------------------------

@pytest.fixture()
def client(postgres_conn):
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from fastapi.testclient import TestClient

    from web.backend.main import app

    return TestClient(app)


def test_recompute_route_defaults_to_protecting_manual_statuses(client, postgres_conn):
    deal_id = _make_deal(postgres_conn, is_clearance=True)
    from web.backend import queries
    queries.set_deal_status(postgres_conn, deal_id, "dismissed")

    resp = client.post("/api/admin/recompute-deal-statuses")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "updated": 0}
    assert _status(postgres_conn, deal_id) == "dismissed"


def test_recompute_route_override_query_param(client, postgres_conn):
    deal_id = _make_deal(postgres_conn, is_clearance=True)
    from web.backend import queries
    queries.set_deal_status(postgres_conn, deal_id, "dismissed")

    resp = client.post("/api/admin/recompute-deal-statuses?override_manual=true")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "updated": 1}
    assert _status(postgres_conn, deal_id) == "active"


def test_reset_department_cache_route(client, postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    dept_id = db.upsert_department(postgres_conn, retailer_id, "dept-1", "Widgets", None)
    db.mark_department_products_listed(postgres_conn, dept_id)

    resp = client.post("/api/admin/reset-department-cache")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "reset": 1}
    assert db.get_department_products_last_listed_at(postgres_conn, dept_id) is None


def test_repair_missing_data_count_route(client, postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    store_id = db.upsert_store(postgres_conn, retailer_id, "store-1", "00000", "Fake Store", None)
    department_id = db.upsert_department(postgres_conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(postgres_conn, retailer_id, "sku-1", "Test Widget", department_id, None, None)
    observation_id = db.insert_price_observation(
        postgres_conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=999, list_price_cents=None, is_clearance=True, is_penny=False,
        fulfillment_state="in_stock", stock_quantity=5, raw_signal={},
    )
    db.upsert_deal_from_observation(postgres_conn, product_id, store_id, observation_id, is_clearance=True, is_penny=False)

    resp = client.get("/api/admin/repair-missing-data/count")

    assert resp.status_code == 200
    assert resp.json() == {"missing": 1}  # no image_url/canonical_url/location on this product
