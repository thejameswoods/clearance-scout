"""The Deals page sidebar/status-bar backing queries: retailer_store_tree,
department_tree_with_counts, status_bar_counts, and list_deals' new
price-range/in-stock/sort options. Also covers the route layer (the
combined GET /api/deals/tree, product dismiss/undismiss, deal defer/undefer)
via FastAPI's TestClient against the real test Postgres.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from common import db
from web.backend import queries

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:test@localhost:5433/postgres"
)


def _seed(conn, *, retailer_slug="home_depot", retailer_name="Home Depot",
          store_id_str="store-1", store_name="Store 1", dept_name="Electrical Batteries",
          sku="sku-1", is_clearance=True, price_cents=500, list_price_cents=1000,
          fulfillment_state="in_stock"):
    retailer_id = db.upsert_retailer(conn, retailer_slug, retailer_name, "https://example.invalid")
    store_id = db.upsert_store(conn, retailer_id, store_id_str, "00000", store_name, None)
    department_id = db.upsert_department(conn, retailer_id, dept_name, dept_name, None)
    product_id = db.upsert_product(conn, retailer_id, sku, "Test Widget", department_id, None, None)
    observation_id = db.insert_price_observation(
        conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=price_cents, list_price_cents=list_price_cents,
        is_clearance=is_clearance, is_penny=False,
        fulfillment_state=fulfillment_state, stock_quantity=5, raw_signal={},
    )
    db.upsert_deal_from_observation(conn, product_id, store_id, observation_id, is_clearance, False)
    return retailer_id, store_id, department_id, product_id


# --- retailer_store_tree ---------------------------------------------------

def test_retailer_store_tree_counts_and_groups(postgres_conn):
    _seed(postgres_conn, retailer_slug="home_depot", store_id_str="s1")
    _seed(postgres_conn, retailer_slug="home_depot", store_id_str="s2", sku="sku-2")
    _seed(postgres_conn, retailer_slug="best_buy", retailer_name="Best Buy", store_id_str="s3", sku="sku-3")

    tree = queries.retailer_store_tree(postgres_conn)

    by_slug = {r["slug"]: r for r in tree}
    assert by_slug["home_depot"]["total"] == 2
    assert len(by_slug["home_depot"]["stores"]) == 2
    assert by_slug["best_buy"]["total"] == 1


def test_retailer_store_tree_excludes_dismissed_products(postgres_conn):
    _, _, _, product_id = _seed(postgres_conn)
    db.dismiss_product(postgres_conn, product_id)

    tree = queries.retailer_store_tree(postgres_conn)
    assert tree[0]["total"] == 0


# --- department_tree_with_counts -------------------------------------------

def test_department_tree_rolls_counts_up_to_ancestors(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "home_depot", "Home Depot", "https://example.invalid")
    store_id = db.upsert_store(postgres_conn, retailer_id, "s1", "00000", "Store 1", None)
    db.upsert_department(postgres_conn, retailer_id, "Electrical", "Electrical", None)
    child_dept = db.upsert_department(postgres_conn, retailer_id, "Electrical Batteries", "Electrical Batteries", None)

    product_id = db.upsert_product(postgres_conn, retailer_id, "sku-1", "Item", child_dept, None, None)
    observation_id = db.insert_price_observation(
        postgres_conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=500, list_price_cents=1000, is_clearance=True, is_penny=False,
        fulfillment_state="in_stock", stock_quantity=5, raw_signal={},
    )
    db.upsert_deal_from_observation(postgres_conn, product_id, store_id, observation_id, True, False)

    tree = queries.department_tree_with_counts(postgres_conn, "home_depot")
    by_name = {n["name"]: n for n in tree}

    assert by_name["Electrical Batteries"]["count"] == 1  # own
    assert by_name["Electrical"]["count"] == 1             # rolled up from child
    assert by_name["Electrical"]["depth"] == 0
    assert by_name["Electrical Batteries"]["depth"] == 1


def test_department_tree_scoped_to_one_store(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "home_depot", "Home Depot", "https://example.invalid")
    store_a = db.upsert_store(postgres_conn, retailer_id, "store-a", "00000", "Store A", None)
    store_b = db.upsert_store(postgres_conn, retailer_id, "store-b", "00000", "Store B", None)
    dept_id = db.upsert_department(postgres_conn, retailer_id, "Electrical", "Electrical", None)
    product_id = db.upsert_product(postgres_conn, retailer_id, "sku-1", "Item", dept_id, None, None)
    obs_id = db.insert_price_observation(
        postgres_conn, product_id, store_a, None, datetime.now(timezone.utc),
        price_cents=500, list_price_cents=1000, is_clearance=True, is_penny=False,
        fulfillment_state="in_stock", stock_quantity=5, raw_signal={},
    )
    db.upsert_deal_from_observation(postgres_conn, product_id, store_a, obs_id, True, False)

    tree_for_a = queries.department_tree_with_counts(postgres_conn, "home_depot", store_id=store_a)
    tree_for_b = queries.department_tree_with_counts(postgres_conn, "home_depot", store_id=store_b)

    assert tree_for_a[0]["count"] == 1
    assert tree_for_b[0]["count"] == 0


# --- status_bar_counts -------------------------------------------------------

def test_status_bar_counts_buckets_correctly(postgres_conn):
    _seed(postgres_conn, sku="sku-active", is_clearance=True)
    _seed(postgres_conn, sku="sku-deferred", is_clearance=True)
    deferred_row = postgres_conn.execute(
        "SELECT d.id FROM deal d JOIN product p ON p.id = d.product_id WHERE p.retailer_product_id = 'sku-deferred'"
    ).fetchone()
    db.defer_deal(postgres_conn, deferred_row["id"], "price", 1.00)

    counts = queries.status_bar_counts(postgres_conn)
    assert counts == {"active": 1, "waiting": 1, "all": 2}


# --- list_deals: new filters -------------------------------------------------

def test_list_deals_price_range_filter(postgres_conn):
    _seed(postgres_conn, sku="cheap", price_cents=200)
    _seed(postgres_conn, sku="mid", price_cents=500)
    _seed(postgres_conn, sku="pricey", price_cents=900)

    rows = queries.list_deals(postgres_conn, price_min_cents=300, price_max_cents=700)
    assert {r["retailer_product_id"] for r in rows} == {"mid"}


def test_list_deals_in_stock_only_filter(postgres_conn):
    _seed(postgres_conn, sku="in-stock", fulfillment_state="in_stock")
    _seed(postgres_conn, sku="oos", fulfillment_state="out_of_stock")

    rows = queries.list_deals(postgres_conn, in_stock_only=True)
    assert {r["retailer_product_id"] for r in rows} == {"in-stock"}


def test_list_deals_sort_oldest_and_price(postgres_conn):
    _seed(postgres_conn, sku="high", price_cents=900)
    _seed(postgres_conn, sku="low", price_cents=100)

    rows = queries.list_deals(postgres_conn, sort="price")
    assert [r["retailer_product_id"] for r in rows] == ["low", "high"]


# --- routes ------------------------------------------------------------------

@pytest.fixture()
def client(postgres_conn):
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from fastapi.testclient import TestClient
    from web.backend.main import app
    return TestClient(app)


def test_tree_route_returns_combined_payload(client, postgres_conn):
    _seed(postgres_conn)

    resp = client.get("/api/deals/tree")

    assert resp.status_code == 200
    body = resp.json()
    assert body["selected_retailer"] == "home_depot"
    assert body["retailers"][0]["total"] == 1
    assert body["departments"][0]["count"] == 1
    assert body["status_counts"]["active"] == 1


def test_dismiss_product_route_and_undo(client, postgres_conn):
    _, _, _, product_id = _seed(postgres_conn)

    resp = client.post(f"/api/products/{product_id}/dismiss")
    assert resp.status_code == 200
    assert queries.list_deals(postgres_conn) == []

    resp = client.post(f"/api/products/{product_id}/undismiss")
    assert resp.status_code == 200
    assert len(queries.list_deals(postgres_conn)) == 1


def test_defer_and_undefer_routes(client, postgres_conn):
    _seed(postgres_conn)
    deal_id = postgres_conn.execute("SELECT id FROM deal").fetchone()["id"]

    resp = client.post(f"/api/deals/{deal_id}/defer", json={"type": "discount_pct", "value": 85})
    assert resp.status_code == 200
    assert postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()["status"] == "deferred"

    resp = client.post(f"/api/deals/{deal_id}/undefer")
    assert resp.status_code == 200
    assert postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()["status"] == "new"


def test_defer_route_rejects_bad_type(client, postgres_conn):
    _seed(postgres_conn)
    deal_id = postgres_conn.execute("SELECT id FROM deal").fetchone()["id"]

    resp = client.post(f"/api/deals/{deal_id}/defer", json={"type": "bogus", "value": 1})
    assert resp.status_code == 400
