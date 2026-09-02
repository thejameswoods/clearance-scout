"""Design-v2 shopping list state model (screens 3a/3b): the list_item
table, its write helpers in common/db.py, the aisle-grouped read model in
web/backend/queries.py, and the routes in web/backend/routes/lists.py.
Also covers the deal.deal_kind/check_interval "Watching" columns (screen
2a's residual status tag) since they live in the same schema change.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from common import db

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:test@localhost:5433/postgres"
)


def _seed_deal(conn, *, sku="sku-1", retailer_store_id="store-1", aisle=None, bay=None,
                price_cents=347, list_price_cents=1198, retailer_slug="fake_retailer"):
    retailer_id = db.upsert_retailer(conn, retailer_slug, "Fake Retailer", "https://example.invalid")
    store_id = db.upsert_store(conn, retailer_id, retailer_store_id, "00000", "Fake Store", "1 Main St")
    department_id = db.upsert_department(conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(conn, retailer_id, sku, "Test Widget", department_id, None, None)
    if aisle is not None or bay is not None:
        db.upsert_store_product_location(conn, product_id, store_id, aisle, bay)
    observation_id = db.insert_price_observation(
        conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=price_cents, list_price_cents=list_price_cents,
        is_clearance=True, is_penny=False,
        fulfillment_state="in_stock", stock_quantity=5, raw_signal={},
    )
    deal_id, _ = db.upsert_deal_from_observation(conn, product_id, store_id, observation_id, True, False)
    return deal_id, product_id, store_id


@pytest.fixture()
def client(postgres_conn):
    # Same pattern as tests/test_retailer_settings_route.py -- point the
    # lazily-read DATABASE_URL at this test's already-schema'd throwaway DB.
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from fastapi.testclient import TestClient

    from web.backend.main import app

    return TestClient(app)


# --- list_item state transitions (common/db.py) -----------------------------

def test_add_deal_to_list_creates_open_item(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)

    row = postgres_conn.execute("SELECT state, quantity FROM list_item WHERE deal_id = %s", (deal_id,)).fetchone()
    assert row["state"] == "open"
    assert row["quantity"] is None


def test_add_deal_to_list_with_quantity(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id, quantity=4)

    row = postgres_conn.execute("SELECT quantity FROM list_item WHERE deal_id = %s", (deal_id,)).fetchone()
    assert row["quantity"] == 4


def test_mark_purchased_sets_state_timestamp_and_dual_writes_deal_status(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)

    db.mark_list_item_purchased(postgres_conn, deal_id)

    item = postgres_conn.execute(
        "SELECT state, purchased_at FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone()
    assert item["state"] == "purchased"
    assert item["purchased_at"] is not None

    deal = postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()
    assert deal["status"] == "bought"  # History still reads deal.status, unaffected by this feature


def test_mark_cant_find_keeps_item_on_the_list(postgres_conn):
    """Handoff requirement: can't-find items STAY on the list, kept for the
    next trip -- unlike no_longer_needed, deal.status stays 'saved'."""
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)

    db.mark_list_item_cant_find(postgres_conn, deal_id, "gone from the shelf")

    item = postgres_conn.execute(
        "SELECT state, cant_find_reason FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone()
    assert item["state"] == "cant_find"
    assert item["cant_find_reason"] == "gone from the shelf"

    deal = postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()
    assert deal["status"] == "saved"

    from web.backend import queries
    stores = queries.store_lists(postgres_conn)
    all_items = [i for s in stores for g in s["aisle_groups"] for i in g["items"]]
    assert any(i["deal_id"] == deal_id for i in all_items)  # still on the list


def test_cant_find_reason_is_optional(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)

    db.mark_list_item_cant_find(postgres_conn, deal_id, None)

    item = postgres_conn.execute(
        "SELECT state, cant_find_reason FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone()
    assert item["state"] == "cant_find"
    assert item["cant_find_reason"] is None


def test_no_longer_needed_removes_from_list_but_does_not_dismiss_product(postgres_conn):
    """The handoff's explicit requirement: no_longer_needed is list-scoped
    and must NOT trigger the permanent, cross-store product.dismissed_at
    flag (that's a separate action -- see common/db.py's dismiss_product)."""
    deal_id, product_id, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)

    db.mark_list_item_no_longer_needed(postgres_conn, deal_id)

    item = postgres_conn.execute("SELECT state FROM list_item WHERE deal_id = %s", (deal_id,)).fetchone()
    assert item["state"] == "no_longer_needed"

    product = postgres_conn.execute(
        "SELECT dismissed_at FROM product WHERE id = %s", (product_id,)
    ).fetchone()
    assert product["dismissed_at"] is None  # NOT dismissed

    from web.backend import queries
    stores = queries.store_lists(postgres_conn)
    all_items = [i for s in stores for g in s["aisle_groups"] for i in g["items"]]
    assert not any(i["deal_id"] == deal_id for i in all_items)  # gone from the list view


def test_reopen_undoes_purchased_and_restores_deal_status(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)
    db.mark_list_item_purchased(postgres_conn, deal_id)

    db.reopen_list_item(postgres_conn, deal_id)

    item = postgres_conn.execute(
        "SELECT state, purchased_at FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone()
    assert item["state"] == "open"
    assert item["purchased_at"] is None

    deal = postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()
    assert deal["status"] == "saved"  # back from 'bought'


def test_reopen_undoes_cant_find(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)
    db.mark_list_item_cant_find(postgres_conn, deal_id, "wrong aisle")

    db.reopen_list_item(postgres_conn, deal_id)

    item = postgres_conn.execute(
        "SELECT state, cant_find_reason FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone()
    assert item["state"] == "open"
    assert item["cant_find_reason"] is None


def test_reopen_undoes_no_longer_needed(postgres_conn):
    """Re-adding a no_longer_needed item back to the list via reopen --
    the walking view's undo doesn't distinguish which resolution it's
    undoing."""
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)
    db.mark_list_item_no_longer_needed(postgres_conn, deal_id)

    db.reopen_list_item(postgres_conn, deal_id)

    from web.backend import queries
    stores = queries.store_lists(postgres_conn)
    all_items = [i for s in stores for g in s["aisle_groups"] for i in g["items"]]
    assert any(i["deal_id"] == deal_id for i in all_items)


def test_readding_a_no_longer_needed_deal_via_save_reopens_it(postgres_conn):
    """add_deal_to_list is what POST /api/deals/{id}/save calls -- clicking
    Want again after marking something no_longer_needed should put it back
    on the list, not error on the UNIQUE(deal_id) constraint."""
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)
    db.mark_list_item_no_longer_needed(postgres_conn, deal_id)

    db.add_deal_to_list(postgres_conn, deal_id)

    item = postgres_conn.execute("SELECT state FROM list_item WHERE deal_id = %s", (deal_id,)).fetchone()
    assert item["state"] == "open"


def test_set_list_item_quantity(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)

    db.set_list_item_quantity(postgres_conn, deal_id, 10)

    assert postgres_conn.execute(
        "SELECT quantity FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone()["quantity"] == 10


# --- clear-finished -----------------------------------------------------

def test_clear_finished_removes_only_purchased_items_for_that_store(postgres_conn):
    purchased_deal, _, store_id = _seed_deal(postgres_conn, sku="sku-purchased", retailer_store_id="store-x")
    open_deal, _, _ = _seed_deal(postgres_conn, sku="sku-open", retailer_store_id="store-x")
    other_store_purchased, _, other_store_id = _seed_deal(
        postgres_conn, sku="sku-other", retailer_store_id="store-y"
    )
    for d in (purchased_deal, open_deal, other_store_purchased):
        db.add_deal_to_list(postgres_conn, d)
    db.mark_list_item_purchased(postgres_conn, purchased_deal)
    db.mark_list_item_purchased(postgres_conn, other_store_purchased)

    cleared = db.clear_finished_list_items(postgres_conn, store_id)

    assert cleared == 1
    remaining_deal_ids = {
        r["deal_id"] for r in postgres_conn.execute("SELECT deal_id FROM list_item").fetchall()
    }
    assert purchased_deal not in remaining_deal_ids
    assert open_deal in remaining_deal_ids
    assert other_store_purchased in remaining_deal_ids  # different store, untouched


def test_clear_finished_leaves_cant_find_items(postgres_conn):
    deal_id, _, store_id = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)
    db.mark_list_item_cant_find(postgres_conn, deal_id, "gone")

    cleared = db.clear_finished_list_items(postgres_conn, store_id)

    assert cleared == 0
    assert postgres_conn.execute("SELECT 1 FROM list_item WHERE deal_id = %s", (deal_id,)).fetchone()


# --- store_lists aisle grouping ------------------------------------------

def test_store_lists_groups_items_by_aisle_ascending(postgres_conn):
    d_aisle12, _, store_id = _seed_deal(postgres_conn, sku="sku-a", retailer_store_id="store-1", aisle="12", bay="004")
    d_aisle9, _, _ = _seed_deal(postgres_conn, sku="sku-b", retailer_store_id="store-1", aisle="09", bay="003")
    for d in (d_aisle12, d_aisle9):
        db.add_deal_to_list(postgres_conn, d)

    from web.backend import queries
    stores = queries.store_lists(postgres_conn)

    assert len(stores) == 1
    aisles = [g["aisle"] for g in stores[0]["aisle_groups"]]
    assert aisles == ["09", "12"]  # ascending, not insertion or string order


def test_store_lists_puts_aisle_unknown_group_last(postgres_conn):
    d_unknown, _, store_id = _seed_deal(postgres_conn, sku="sku-unknown", retailer_store_id="store-1")
    d_aisle26, _, _ = _seed_deal(postgres_conn, sku="sku-c", retailer_store_id="store-1", aisle="26", bay="004")
    d_aisle9, _, _ = _seed_deal(postgres_conn, sku="sku-b", retailer_store_id="store-1", aisle="09", bay="003")
    for d in (d_unknown, d_aisle26, d_aisle9):
        db.add_deal_to_list(postgres_conn, d)

    from web.backend import queries
    stores = queries.store_lists(postgres_conn)

    groups = stores[0]["aisle_groups"]
    assert [g["aisle"] for g in groups] == ["09", "26", None]
    assert groups[-1]["items"][0]["deal_id"] == d_unknown


def test_store_lists_excludes_no_longer_needed_items_and_empty_stores(postgres_conn):
    deal_id, _, store_id = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)
    db.mark_list_item_no_longer_needed(postgres_conn, deal_id)

    from web.backend import queries
    stores = queries.store_lists(postgres_conn)

    assert stores == []  # the only item on this store's list was removed -- no card at all


def test_store_lists_counts(postgres_conn):
    open_deal, _, store_id = _seed_deal(postgres_conn, sku="sku-open", retailer_store_id="store-1")
    purchased_deal, _, _ = _seed_deal(postgres_conn, sku="sku-purchased", retailer_store_id="store-1")
    cant_find_deal, _, _ = _seed_deal(postgres_conn, sku="sku-cant-find", retailer_store_id="store-1")
    for d in (open_deal, purchased_deal, cant_find_deal):
        db.add_deal_to_list(postgres_conn, d)
    db.mark_list_item_purchased(postgres_conn, purchased_deal)
    db.mark_list_item_cant_find(postgres_conn, cant_find_deal, None)

    from web.backend import queries
    stores = queries.store_lists(postgres_conn)

    assert stores[0]["counts"] == {"total": 3, "open": 1, "purchased": 1, "cant_find": 1}


def test_store_lists_items_carry_deal_kind_and_clearance_penny_flags(postgres_conn):
    """Walking view's promoted card states why an item was flagged
    (screen 3b, "yellow-tag clearance" in the wireframe) -- it needs
    deal.deal_kind and the latest observation's is_clearance/is_penny on
    each list item, not a price-based guess. See app.js's flagReasonText."""
    clearance_deal, _, store_id = _seed_deal(postgres_conn, sku="sku-clearance", retailer_store_id="store-1")
    db.add_deal_to_list(postgres_conn, clearance_deal)

    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    department_id = db.upsert_department(postgres_conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(postgres_conn, retailer_id, "sku-penny", "Penny Item", department_id, None, None)
    observation_id = db.insert_price_observation(
        postgres_conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=1, list_price_cents=1999, is_clearance=True, is_penny=True,
        fulfillment_state="in_stock", stock_quantity=1, raw_signal={},
    )
    penny_deal, _ = db.upsert_deal_from_observation(postgres_conn, product_id, store_id, observation_id, True, True)
    db.add_deal_to_list(postgres_conn, penny_deal)

    from web.backend import queries
    stores = queries.store_lists(postgres_conn)
    items = {i["deal_id"]: i for s in stores for g in s["aisle_groups"] for i in g["items"]}

    assert items[clearance_deal]["deal_kind"] == "active_clearance"
    assert items[clearance_deal]["is_clearance"] is True
    assert items[clearance_deal]["is_penny"] is False

    assert items[penny_deal]["deal_kind"] == "penny"
    assert items[penny_deal]["is_clearance"] is True
    assert items[penny_deal]["is_penny"] is True


# --- deal_kind / check_interval / last_checked_at (Watching) ----------------

def test_new_deal_defaults_to_active_clearance_kind_with_last_checked_set(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    row = postgres_conn.execute(
        "SELECT deal_kind, last_checked_at FROM deal WHERE id = %s", (deal_id,)
    ).fetchone()
    assert row["deal_kind"] == "active_clearance"
    assert row["last_checked_at"] is not None


def test_penny_hit_gets_penny_deal_kind(postgres_conn):
    retailer_id = db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    store_id = db.upsert_store(postgres_conn, retailer_id, "store-1", "00000", "Fake Store", None)
    department_id = db.upsert_department(postgres_conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(postgres_conn, retailer_id, "sku-penny", "Penny Item", department_id, None, None)
    observation_id = db.insert_price_observation(
        postgres_conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=1, list_price_cents=1999, is_clearance=True, is_penny=True,
        fulfillment_state="in_stock", stock_quantity=1, raw_signal={},
    )
    deal_id, _ = db.upsert_deal_from_observation(postgres_conn, product_id, store_id, observation_id, True, True)

    assert postgres_conn.execute(
        "SELECT deal_kind FROM deal WHERE id = %s", (deal_id,)
    ).fetchone()["deal_kind"] == "penny"


def test_shorten_check_interval_halves_it(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    before = postgres_conn.execute(
        "SELECT extract(epoch FROM check_interval)::int AS seconds FROM deal WHERE id = %s", (deal_id,)
    ).fetchone()["seconds"]

    db.shorten_check_interval(postgres_conn, deal_id)

    after = postgres_conn.execute(
        "SELECT extract(epoch FROM check_interval)::int AS seconds FROM deal WHERE id = %s", (deal_id,)
    ).fetchone()["seconds"]
    assert after == before // 2


def test_shorten_check_interval_floors_at_fifteen_minutes(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    for _ in range(10):  # repeatedly halving a 4h interval would go well under 15m without a floor
        db.shorten_check_interval(postgres_conn, deal_id)

    seconds = postgres_conn.execute(
        "SELECT extract(epoch FROM check_interval)::int AS seconds FROM deal WHERE id = %s", (deal_id,)
    ).fetchone()["seconds"]
    assert seconds == 15 * 60


def test_watching_status_count(postgres_conn):
    """status_bar_counts' new "watching" tag -- counts deal_kind =
    'upcoming_clearance' rows. Nothing in the scanner sets that kind yet
    (out of this agent's scope, see docs/schema-changes-design-v2.md), so
    this test sets it directly to prove the read side is wired correctly
    ahead of that write path landing."""
    watching_deal, _, _ = _seed_deal(postgres_conn, sku="sku-watch", retailer_store_id="store-1")
    active_deal, _, _ = _seed_deal(postgres_conn, sku="sku-active", retailer_store_id="store-1")
    postgres_conn.execute("UPDATE deal SET deal_kind = 'upcoming_clearance' WHERE id = %s", (watching_deal,))

    from web.backend import queries
    counts = queries.status_bar_counts(postgres_conn)

    assert counts["watching"] == 1
    assert counts["active"] == 1
    assert counts["all"] == 2


def test_list_deals_exposes_watching_fields(postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    postgres_conn.execute("UPDATE deal SET deal_kind = 'upcoming_clearance' WHERE id = %s", (deal_id,))

    from web.backend import queries
    rows = queries.list_deals(postgres_conn)

    assert rows[0]["deal_kind"] == "upcoming_clearance"
    assert rows[0]["check_interval_seconds"] == 4 * 3600
    assert rows[0]["last_checked_at"] is not None


# --- API layer (web/backend/routes/lists.py + deals.py's save/close-eye) ---

def test_get_lists_route_returns_aisle_grouped_stores(client, postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn, aisle="09", bay="003")
    db.add_deal_to_list(postgres_conn, deal_id)

    resp = client.get("/api/lists")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_stores"] == 1
    assert body["total_items"] == 1
    assert body["stores"][0]["aisle_groups"][0]["aisle"] == "09"


def test_purchased_cant_find_no_longer_needed_and_reopen_routes(client, postgres_conn):
    deal_id, product_id, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)

    resp = client.post(f"/api/lists/items/{deal_id}/cant-find", json={"reason": "wrong aisle"})
    assert resp.status_code == 200
    assert postgres_conn.execute(
        "SELECT state, cant_find_reason FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone() == {"state": "cant_find", "cant_find_reason": "wrong aisle"}

    resp = client.post(f"/api/lists/items/{deal_id}/reopen")
    assert resp.status_code == 200
    assert postgres_conn.execute(
        "SELECT state FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone()["state"] == "open"

    resp = client.post(f"/api/lists/items/{deal_id}/purchased")
    assert resp.status_code == 200

    resp = client.post(f"/api/lists/items/{deal_id}/no-longer-needed")
    assert resp.status_code == 200
    assert postgres_conn.execute(
        "SELECT dismissed_at FROM product WHERE id = %s", (product_id,)
    ).fetchone()["dismissed_at"] is None


def test_quantity_route(client, postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)

    resp = client.put(f"/api/lists/items/{deal_id}/quantity", json={"quantity": 6})

    assert resp.status_code == 200
    assert postgres_conn.execute(
        "SELECT quantity FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone()["quantity"] == 6


def test_clear_finished_route(client, postgres_conn):
    deal_id, _, store_id = _seed_deal(postgres_conn)
    db.add_deal_to_list(postgres_conn, deal_id)
    db.mark_list_item_purchased(postgres_conn, deal_id)

    resp = client.post(f"/api/lists/store/{store_id}/clear-finished")

    assert resp.status_code == 200
    assert resp.json()["cleared"] == 1
    assert postgres_conn.execute("SELECT 1 FROM list_item WHERE deal_id = %s", (deal_id,)).fetchone() is None


def test_save_route_still_works_and_creates_a_list_item(client, postgres_conn):
    """Explicit regression test for the handoff's requirement: the
    pre-existing POST /api/deals/{id}/save must keep working unchanged."""
    deal_id, _, _ = _seed_deal(postgres_conn)

    resp = client.post(f"/api/deals/{deal_id}/save")

    assert resp.status_code == 200
    assert postgres_conn.execute("SELECT status FROM deal WHERE id = %s", (deal_id,)).fetchone()["status"] == "saved"
    assert postgres_conn.execute(
        "SELECT state FROM list_item WHERE deal_id = %s", (deal_id,)
    ).fetchone()["state"] == "open"


def test_close_eye_route(client, postgres_conn):
    deal_id, _, _ = _seed_deal(postgres_conn)

    resp = client.post(f"/api/deals/{deal_id}/close-eye")

    assert resp.status_code == 200
    seconds = postgres_conn.execute(
        "SELECT extract(epoch FROM check_interval)::int AS s FROM deal WHERE id = %s", (deal_id,)
    ).fetchone()["s"]
    assert seconds == 2 * 3600  # halved from the 4h default
