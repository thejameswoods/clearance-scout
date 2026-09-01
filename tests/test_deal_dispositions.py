"""The Deals-page disposition model: product-level dismiss ("Not
interested"), per-store defer ("Not yet"), and its scan-time reactivation.
Covers common/db.py directly and the orchestrator hook that calls
reactivate_satisfied_defers once per scan.
"""

from __future__ import annotations

from datetime import datetime, timezone

from adapters.base import Department, ProductRef
from common import db
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _seed_deal(conn, *, sku="sku-1", retailer_store_id="store-1", is_clearance=True,
                price_cents=999, list_price_cents=1999, is_penny=False):
    retailer_id = db.upsert_retailer(conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    store_id = db.upsert_store(conn, retailer_id, retailer_store_id, "00000", "Fake Store", None)
    department_id = db.upsert_department(conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(conn, retailer_id, sku, "Test Widget", department_id, None, None)
    observation_id = db.insert_price_observation(
        conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=price_cents, list_price_cents=list_price_cents,
        is_clearance=is_clearance, is_penny=is_penny,
        fulfillment_state="in_stock", stock_quantity=5, raw_signal={},
    )
    deal_id, _ = db.upsert_deal_from_observation(conn, product_id, store_id, observation_id, is_clearance, is_penny)
    return deal_id, product_id, store_id


# --- dismiss_product / undismiss_product ---------------------------------

def test_dismiss_product_excludes_it_from_every_store(postgres_conn):
    _, product_id, store_a = _seed_deal(postgres_conn, sku="sku-1", retailer_store_id="store-a")
    _, same_product_id, store_b = _seed_deal(postgres_conn, sku="sku-1", retailer_store_id="store-b")
    assert product_id == same_product_id  # same product, two stores

    from web.backend import queries
    before = queries.list_deals(postgres_conn)
    assert len(before) == 2

    db.dismiss_product(postgres_conn, product_id)

    after = queries.list_deals(postgres_conn)
    assert after == []  # excluded at both stores, from one product-level flag


def test_dismiss_product_also_sets_deal_status_for_history(postgres_conn):
    """History (loadHistory in app.js) still reads deal.status, unaffected
    by this feature -- dismiss_product must dual-write it, not just the
    new product.dismissed_at flag, or a product dismissed via the new
    Deals-page UI becomes invisible everywhere, including History."""
    deal_id, product_id, _ = _seed_deal(postgres_conn)
    db.dismiss_product(postgres_conn, product_id)

    row = postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()
    assert row["status"] == "dismissed"

    from web.backend import queries
    history_rows = queries.list_deals(postgres_conn, status=["dismissed"])
    assert len(history_rows) == 1


def test_undismiss_product_restores_it(postgres_conn):
    _, product_id, _ = _seed_deal(postgres_conn)
    db.dismiss_product(postgres_conn, product_id)
    db.undismiss_product(postgres_conn, product_id)

    from web.backend import queries
    assert len(queries.list_deals(postgres_conn)) == 1


# --- defer_deal / undefer_deal --------------------------------------------

def test_defer_deal_sets_status_and_rule(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.defer_deal(postgres_conn, deal_id, "discount_pct", 80)

    row = postgres_conn.execute("SELECT status, defer_rule FROM deal WHERE id = %s", (deal_id,)).fetchone()
    assert row["status"] == "deferred"
    assert row["defer_rule"] == {"type": "discount_pct", "value": 80}


def test_defer_deal_rejects_unknown_type(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    try:
        db.defer_deal(postgres_conn, deal_id, "bogus", 1)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_undefer_deal_clears_back_to_new(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.defer_deal(postgres_conn, deal_id, "penny", None)
    db.undefer_deal(postgres_conn, deal_id)

    row = postgres_conn.execute("SELECT status, defer_rule FROM deal WHERE id = %s", (deal_id,)).fetchone()
    assert row["status"] == "new"
    assert row["defer_rule"] is None


def test_deferred_deal_is_excluded_from_the_default_feed(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.defer_deal(postgres_conn, deal_id, "price", 3.00)

    from web.backend import queries
    assert queries.list_deals(postgres_conn) == []
    assert queries.list_deals(postgres_conn, status=["deferred"])[0]["deal_id"] == deal_id


# --- reactivate_satisfied_defers -------------------------------------------

def test_reactivate_satisfied_defers_price_threshold(postgres_conn):
    deal_id, product_id, store_id = _seed_deal(postgres_conn, price_cents=500)
    db.defer_deal(postgres_conn, deal_id, "price", 3.00)  # not yet satisfied at $5.00

    count = db.reactivate_satisfied_defers(postgres_conn)
    assert count == 0
    assert postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()["status"] == "deferred"

    # A fresh observation drops the price to $2.50 -- now satisfies "<= $3".
    obs_id = db.insert_price_observation(
        postgres_conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=250, list_price_cents=1999, is_clearance=True, is_penny=False,
        fulfillment_state="in_stock", stock_quantity=5, raw_signal={},
    )
    assert obs_id  # keep the observation even though this deal's own row wasn't updated by it

    count = db.reactivate_satisfied_defers(postgres_conn)
    assert count == 1
    row = postgres_conn.execute("SELECT status, defer_rule FROM deal WHERE id = %s", (deal_id,)).fetchone()
    assert row["status"] == "new"
    assert row["defer_rule"] is None


def test_reactivate_satisfied_defers_at_a_different_store(postgres_conn):
    """The design doc's requirement: a threshold set at one store
    reactivates if a DIFFERENT store of the same product satisfies it."""
    deal_id, product_id, store_a = _seed_deal(postgres_conn, sku="sku-1", retailer_store_id="store-a", price_cents=500)
    db.defer_deal(postgres_conn, deal_id, "discount_pct", 80)  # 500/1999 = ~75% off, not enough

    # A different store of the SAME product hits 90% off.
    _, _, store_b = _seed_deal(postgres_conn, sku="sku-1", retailer_store_id="store-b",
                                price_cents=100, list_price_cents=1000)

    count = db.reactivate_satisfied_defers(postgres_conn)

    assert count == 1
    assert postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()["status"] == "new"


def test_reactivate_satisfied_defers_penny(postgres_conn):
    deal_id, product_id, store_id = _seed_deal(postgres_conn, is_penny=False)
    db.defer_deal(postgres_conn, deal_id, "penny", None)

    db.insert_price_observation(
        postgres_conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=1, list_price_cents=1999, is_clearance=True, is_penny=True,
        fulfillment_state="in_stock", stock_quantity=1, raw_signal={},
    )

    count = db.reactivate_satisfied_defers(postgres_conn)
    assert count == 1


def test_reactivate_satisfied_defers_ignores_unrelated_deferred_deals(postgres_conn):
    deal_id, product_id, store_id = _seed_deal(postgres_conn, price_cents=999)
    db.defer_deal(postgres_conn, deal_id, "price", 1.00)  # nowhere close

    count = db.reactivate_satisfied_defers(postgres_conn)
    assert count == 0
    assert postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()["status"] == "deferred"


# --- orchestrator hook ------------------------------------------------------

def test_run_scan_reactivates_satisfied_defers(postgres_conn):
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    ref = ProductRef(retailer_product_id="sku-1", name="Item", department=dept)
    adapter = ConfigurableFakeAdapter(
        departments=[dept], products_by_department={"dept-1": [ref]}, price_cents=100,
    )
    # First scan creates the deal (fake adapter always reports is_clearance=True).
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")
    deal_id = postgres_conn.execute("SELECT id FROM deal").fetchone()["id"]
    db.defer_deal(postgres_conn, deal_id, "price", 5.00)  # $1.00 < $5.00 -- already satisfied

    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    assert postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()["status"] == "new"
