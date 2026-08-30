"""Proves the RetailerAdapter abstraction actually holds: a trivial
in-memory fake retailer runs through the real orchestrator with zero
Home-Depot-specific code touched. A future real adapter (Lowe's, etc.)
should pass an equivalent test before being trusted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from adapters.base import (
    AuthResult,
    ClearanceSignal,
    Department,
    PriceObservation,
    ProductRef,
    RateLimitPolicy,
    RetailerAdapter,
    StoreInfo,
)
from scanner.orchestrator import run_scan


class FakeRetailerAdapter(RetailerAdapter):
    retailer_slug = "fake_retailer"

    def authenticate(self, browser_ctx) -> AuthResult:
        return AuthResult(valid=True)

    def set_store(self, browser_ctx, zip_code: str) -> StoreInfo:
        return StoreInfo(retailer_store_id="store-1", zip_code=zip_code, name="Fake Store")

    def discover_departments(self, browser_ctx) -> Iterator[Department]:
        yield Department(retailer_department_id="dept-1", name="Widgets")

    def list_products(self, browser_ctx, department: Department) -> Iterator[ProductRef]:
        yield ProductRef(retailer_product_id="sku-1", name="Test Widget", department=department)

    def check_price(self, browser_ctx, product_ref: ProductRef, store: StoreInfo) -> PriceObservation:
        return PriceObservation(
            product_ref=product_ref,
            store=store,
            observed_at=datetime.now(timezone.utc),
            price_cents=999,
            list_price_cents=1999,
            is_clearance=True,
            fulfillment_state="in_stock",
        )

    def detect_clearance(self, raw_response) -> ClearanceSignal | None:
        return ClearanceSignal(is_clearance=True, reason="fixture")

    def detect_penny(self, observation: PriceObservation) -> bool:
        return observation.price_cents == 1

    def rate_limit_policy(self) -> RateLimitPolicy:
        return RateLimitPolicy(min_delay_seconds=0, max_delay_seconds=0)


class FakeBrowserContext:
    """Stands in for a Playwright BrowserContext — the fake adapter never
    actually touches it, but the orchestrator sets clearance_scout_store_id
    on it (see orchestrator.py), so it needs to accept attribute writes."""


def test_orchestrator_runs_fake_adapter_end_to_end(postgres_conn):
    ctx = FakeBrowserContext()
    result = run_scan(postgres_conn, ctx, FakeRetailerAdapter(), zip_code="00000", trigger="manual")

    assert result["departments_scanned"] == 1
    assert result["products_checked"] == 1
    assert result["errors_count"] == 0
    assert len(result["new_deal_product_ids"]) == 1
