"""run_scan()'s on_progress callback -- feeds the dashboard's live status
view (scanner/main.py's /status) so "what's it doing right now" doesn't
require reading logs or the DB by hand."""

from __future__ import annotations

from adapters.base import Department, ProductRef
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


def test_no_progress_callback_is_fine(postgres_conn):
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    adapter = ConfigurableFakeAdapter(departments=[dept])

    # Should not raise just because on_progress wasn't provided.
    run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")
