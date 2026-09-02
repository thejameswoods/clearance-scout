"""Shared test doubles. A configurable in-memory RetailerAdapter so tests
can exercise the orchestrator's generic logic (filtering, multi-store
iteration, ...) without any retailer-specific code or network/browser
involved.
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


class FakeBrowserContext:
    """Stands in for a Playwright BrowserContext. The fake adapter never
    touches it for real, but the orchestrator writes
    clearance_scout_store_id onto it, so it needs to accept attribute
    writes."""


class ConfigurableFakeAdapter(RetailerAdapter):
    retailer_slug = "fake_retailer"
    retailer_display_name = "Fake Retailer"

    def __init__(
        self,
        stores: list[StoreInfo] | None = None,
        departments: list[Department] | None = None,
        products_by_department: dict[str, list[ProductRef]] | None = None,
        price_cents: int = 999,
        permission_error_skus: set[str] | None = None,
        failing_skus: set[str] | None = None,
    ):
        self.stores = stores or [StoreInfo(retailer_store_id="store-1", zip_code="00000", name="Fake Store 1")]
        self.departments = departments or [Department(retailer_department_id="dept-1", name="Widgets")]
        self.products_by_department = products_by_department or {
            self.departments[0].retailer_department_id: [
                ProductRef(retailer_product_id="sku-1", name="Test Widget", department=self.departments[0])
            ]
        }
        self.price_cents = price_cents
        self.permission_error_skus = permission_error_skus or set()
        self.failing_skus = failing_skus or set()
        self.list_products_call_count = 0
        self.discover_departments_call_count = 0

    def authenticate(self, browser_ctx) -> AuthResult:
        return AuthResult(valid=True)

    def find_stores(self, browser_ctx, zip_code: str, radius_miles: float) -> Iterator[StoreInfo]:
        yield from self.stores

    def select_store(self, browser_ctx, store: StoreInfo) -> None:
        browser_ctx.clearance_scout_store_id = store.retailer_store_id

    def discover_departments(self, browser_ctx) -> Iterator[Department]:
        self.discover_departments_call_count += 1
        yield from self.departments

    def list_products(self, browser_ctx, department: Department) -> Iterator[ProductRef]:
        self.list_products_call_count += 1
        yield from self.products_by_department.get(department.retailer_department_id, [])

    def check_price(self, browser_ctx, product_ref: ProductRef, store: StoreInfo) -> PriceObservation:
        if product_ref.retailer_product_id in self.permission_error_skus:
            raise PermissionError(f"fake 403 for {product_ref.retailer_product_id}")
        if product_ref.retailer_product_id in self.failing_skus:
            raise RuntimeError(f"fake failure for {product_ref.retailer_product_id}")
        return PriceObservation(
            product_ref=product_ref,
            store=store,
            observed_at=datetime.now(timezone.utc),
            price_cents=self.price_cents,
            list_price_cents=self.price_cents * 2,
            is_clearance=True,
            fulfillment_state="in_stock",
        )

    def detect_clearance(self, raw_response) -> ClearanceSignal | None:
        return ClearanceSignal(is_clearance=True, reason="fixture")

    def detect_penny(self, observation: PriceObservation) -> bool:
        return observation.price_cents == 1

    def rate_limit_policy(self) -> RateLimitPolicy:
        return RateLimitPolicy(min_delay_seconds=0, max_delay_seconds=0)
