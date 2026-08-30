"""Proves the RetailerAdapter abstraction actually holds: a trivial
in-memory fake retailer runs through the real orchestrator with zero
Home-Depot-specific code touched. A future real adapter (Lowe's, etc.)
should pass an equivalent test before being trusted.
"""

from __future__ import annotations

from scanner.orchestrator import run_scan
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def test_orchestrator_runs_fake_adapter_end_to_end(postgres_conn):
    adapter = ConfigurableFakeAdapter()

    result = run_scan(postgres_conn, FakeBrowserContext(), adapter, zip_code="00000", trigger="manual")

    assert result["stores_scanned"] == 1
    assert result["departments_scanned"] == 1
    assert result["products_checked"] == 1
    assert result["errors_count"] == 0
    assert len(result["new_deal_product_ids"]) == 1
