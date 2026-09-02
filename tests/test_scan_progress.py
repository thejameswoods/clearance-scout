"""run_scan()'s on_progress callback -- feeds the dashboard's live status
view (scanner/main.py's /status) so "what's it doing right now" doesn't
require reading logs or the DB by hand."""

from __future__ import annotations

from adapters.base import Department, ProductRef, StoreInfo
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def test_progress_callback_fires_at_key_checkpoints(postgres_conn):
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {"dept-1": [ProductRef(retailer_product_id=f"sku-{i}", name=f"Item {i}", department=dept) for i in range(3)]}
    adapter = ConfigurableFakeAdapter(departments=[dept], products_by_department=products)

    events = []
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", on_progress=events.append)

    phases = [e["phase"] for e in events]
    assert "stores" in phases
    assert "departments" in phases
    assert "store" in phases
    assert "prices" in phases

    # The final "prices" event should report the department fully checked.
    price_events = [e for e in events if e["phase"] == "prices"]
    assert price_events[-1]["department_products_checked"] == 3
    assert price_events[-1]["department_products_total"] == 3


def test_department_index_resets_per_store(postgres_conn):
    """Confirmed live 2026-09-01: on a 2-store scan, department_index kept
    climbing past departments_total once store 2 started (e.g. "277/196")
    instead of resetting -- it was reusing the scan-lifetime department
    counter (correct for the final departments_scanned summary) as the
    per-store progress index (wrong -- departments_total in the same event
    is per-store, from len(departments))."""
    depts = [
        Department(retailer_department_id="dept-1", name="Widgets"),
        Department(retailer_department_id="dept-2", name="Gadgets"),
    ]
    products = {
        "dept-1": [ProductRef(retailer_product_id="sku-1", name="Item 1", department=depts[0])],
        "dept-2": [ProductRef(retailer_product_id="sku-2", name="Item 2", department=depts[1])],
    }
    stores = [
        StoreInfo(retailer_store_id="store-1", zip_code="00000", name="Store 1"),
        StoreInfo(retailer_store_id="store-2", zip_code="00000", name="Store 2"),
    ]
    adapter = ConfigurableFakeAdapter(stores=stores, departments=depts, products_by_department=products)

    events = []
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", on_progress=events.append)

    price_events = [e for e in events if e["phase"] == "prices"]
    assert price_events, "expected at least one 'prices' progress event"
    for event in price_events:
        assert event["department_index"] <= event["departments_total"], (
            f"department_index {event['department_index']} exceeded "
            f"departments_total {event['departments_total']}"
        )
    # 2 departments x 2 "prices" events each (post-listing, department-done) x
    # 2 stores -- index climbs 1, 2 within a store and resets to 1 for the
    # next store instead of continuing on to 3, 4.
    assert [e["department_index"] for e in price_events] == [1, 1, 2, 2, 1, 1, 2, 2]


def test_no_progress_callback_is_fine(postgres_conn):
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    adapter = ConfigurableFakeAdapter(departments=[dept])

    # Should not raise just because on_progress wasn't provided.
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")


def test_avg_department_size_reported_once_department_size_known(postgres_conn):
    """Feeds scanner/settings.py's eta_seconds (header wireframe 5b's
    "~6 min left") -- a running average across departments seen so far
    this scan, not per-poll recomputation."""
    depts = [
        Department(retailer_department_id="dept-1", name="Widgets"),
        Department(retailer_department_id="dept-2", name="Gadgets"),
    ]
    products = {
        "dept-1": [ProductRef(retailer_product_id=f"sku-{i}", name=f"Item {i}", department=depts[0]) for i in range(4)],
        "dept-2": [ProductRef(retailer_product_id=f"sku-{i}", name=f"Item {i}", department=depts[1]) for i in range(2)],
    }
    adapter = ConfigurableFakeAdapter(departments=depts, products_by_department=products)

    events = []
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", on_progress=events.append)

    avg_sizes = [e["avg_department_size"] for e in events if "avg_department_size" in e]
    assert avg_sizes == [4.0, 3.0]  # dept-1 alone (4), then (4+2)/2 once dept-2's size is known


def test_price_checks_total_reported_and_monotonic(postgres_conn):
    """Feeds the header odometer (wireframe 5b) -- run_scan bumps the
    shared counter (common/db.py's increment_price_check_total) once per
    successfully-checked product (persisted to Postgres every time), and
    reports the running total via on_progress at the same cadence as the
    existing heartbeat/department-done checkpoints (not a new event per
    item -- see the comment above price_checks_total's assignment in
    orchestrator.py) so every progress event still carries a full,
    consistent field set."""
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {"dept-1": [ProductRef(retailer_product_id=f"sku-{i}", name=f"Item {i}", department=dept) for i in range(25)]}
    adapter = ConfigurableFakeAdapter(departments=[dept], products_by_department=products)

    events = []
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", on_progress=events.append)

    totals = [e["price_checks_total"] for e in events if "price_checks_total" in e]
    # Heartbeats at i=10, i=20, then the final department-done event at 25.
    assert totals == [10, 20, 25]

    from common import db
    assert db.increment_price_check_total(postgres_conn, by=0) == 25  # persisted, not just in-memory
