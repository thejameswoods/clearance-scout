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
