"""Cooperative cancel (header wireframe 5b's "Cancel scan"): run_scan's
`is_cancelled` checkpoints, checked at the same points on_progress already
fires at (store start, department start, price-check heartbeat) -- see
scanner/orchestrator.py's ScanCancelled. No process is ever killed; a
cancelled scan must close out its in-flight scan_run as 'cancelled'
(db/init/001_schema.sql) rather than leaving it 'running' forever."""

from __future__ import annotations

from adapters.base import Department, ProductRef
from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _cancel_after(n_calls: int):
    """A stand-in for scanner/main.py's _cancel_event.is_set -- returns
    False for the first n_calls-1 checkpoint checks, then True forever
    after, so a test can pin down exactly which checkpoint trips it."""
    state = {"calls": 0}

    def _is_cancelled() -> bool:
        state["calls"] += 1
        return state["calls"] >= n_calls

    return _is_cancelled


def _make_adapter(product_count: int):
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    products = {
        "dept-1": [
            ProductRef(retailer_product_id=f"sku-{i}", name=f"Item {i}", department=dept)
            for i in range(product_count)
        ]
    }
    return ConfigurableFakeAdapter(departments=[dept], products_by_department=products)


def test_cancel_at_heartbeat_stops_mid_department(postgres_conn):
    # Checkpoint order for one store/one department: #1 store-top,
    # #2 department-top, #3 first heartbeat (i=10 of 25 -- 25 % 10 != 0,
    # so no heartbeat fires at the very last item). Tripping on call #3
    # lands cancellation exactly at i=10.
    adapter = _make_adapter(25)

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        is_cancelled=_cancel_after(3),
    )

    assert result["cancelled"] is True
    assert result["products_checked"] == 10  # stopped right after the i=10 heartbeat

    prices_run = postgres_conn.execute(
        "SELECT status, products_checked FROM scan_run WHERE phase = 'prices' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert prices_run["status"] == "cancelled"
    assert prices_run["products_checked"] == 10

    # The whole point of a cooperative cancel: nothing gets left 'running'
    # forever once run_scan has returned.
    still_running = postgres_conn.execute("SELECT COUNT(*) AS n FROM scan_run WHERE status = 'running'").fetchone()
    assert still_running["n"] == 0


def test_cancel_before_any_price_check_leaves_nothing_running(postgres_conn):
    # Trips on the very first checkpoint (store-top) -- nothing has been
    # opened yet at that point (the department-listing scan_run before it
    # is always started+finished in one step), so there's no 'prices'
    # scan_run to close out at all.
    adapter = _make_adapter(5)

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        is_cancelled=_cancel_after(1),
    )

    assert result["cancelled"] is True
    assert result["products_checked"] == 0

    still_running = postgres_conn.execute("SELECT COUNT(*) AS n FROM scan_run WHERE status = 'running'").fetchone()
    assert still_running["n"] == 0
    prices_runs = postgres_conn.execute("SELECT COUNT(*) AS n FROM scan_run WHERE phase = 'prices'").fetchone()
    assert prices_runs["n"] == 0  # never even started


def test_is_cancelled_returning_false_never_interrupts_a_scan(postgres_conn):
    adapter = _make_adapter(5)

    result = run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        is_cancelled=lambda: False,
    )

    assert result["cancelled"] is False
    assert result["products_checked"] == 5


def test_no_is_cancelled_callback_is_fine(postgres_conn):
    # Matches test_scan_progress.py's test_no_progress_callback_is_fine --
    # is_cancelled is optional, same as on_progress.
    adapter = _make_adapter(3)

    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000")

    assert result["cancelled"] is False
    assert result["products_checked"] == 3


def test_cancel_reported_via_on_progress(postgres_conn):
    adapter = _make_adapter(25)
    events = []

    run_scan(
        postgres_conn, FakeBrowserContext(), adapter, zip_code="00000",
        on_progress=events.append, is_cancelled=_cancel_after(3),
    )

    phases = [e["phase"] for e in events if "phase" in e]
    assert "cancelled" in phases
