from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

from ..base import (
    AuthResult,
    Department,
    NeedsLogin,
    PriceObservation,
    ProductRef,
    RateLimitPolicy,
    RetailerAdapter,
    StoreInfo,
)
from .api_client import HomeDepotApiClient
from .clearance import detect_clearance as _detect_clearance
from .departments import discover_departments as _discover_departments
from .penny import detect_penny as _detect_penny

HOME_URL = "https://www.homedepot.com"


class HomeDepotAdapter(RetailerAdapter):
    retailer_slug = "home_depot"

    def authenticate(self, browser_ctx: Any) -> AuthResult:
        # A logged-out session gets redirected to a login/signin page.
        # Adjust the check below once you've observed the real redirect
        # target/marker in a logged-out browser_ctx (see adapters/README.md
        # — this is a human-verifiable one-time check, not a guess to spend
        # effort perfecting blind).
        page = browser_ctx.new_page()
        try:
            page.goto(f"{HOME_URL}/myaccount/", wait_until="domcontentloaded")
            logged_in = "signin" not in page.url and "login" not in page.url
        finally:
            page.close()

        if not logged_in:
            raise NeedsLogin(
                "Home Depot session is not authenticated — log in manually "
                "over noVNC (see docs/deploy-generic.md)."
            )
        return AuthResult(valid=True)

    def find_stores(
        self, browser_ctx: Any, zip_code: str, radius_miles: float
    ) -> Iterator[StoreInfo]:
        client = HomeDepotApiClient(browser_ctx)
        response = client.store_search(zip_code, radius_miles)
        for raw in response.get("stores", []):
            yield StoreInfo(
                retailer_store_id=str(raw["storeId"]),
                zip_code=zip_code,
                name=raw.get("name"),
                address=raw.get("address"),
                distance_miles=raw.get("distance"),
            )

    def select_store(self, browser_ctx: Any, store: StoreInfo) -> None:
        # Home Depot's site typically needs the store set via a page
        # interaction/cookie, not just an API param — confirm the real
        # mechanism from captured traffic (adapters/home_depot/api_client.py)
        # and do it here if so. Recording the id on the context is enough
        # for _require_store_id() either way.
        browser_ctx.clearance_scout_store_id = store.retailer_store_id

    def discover_departments(self, browser_ctx: Any) -> Iterator[Department]:
        client = HomeDepotApiClient(browser_ctx)
        store_id = self._require_store_id(browser_ctx)
        yield from _discover_departments(client, store_id)

    def list_products(
        self, browser_ctx: Any, department: Department
    ) -> Iterator[ProductRef]:
        client = HomeDepotApiClient(browser_ctx)
        store_id = self._require_store_id(browser_ctx)
        response = client.department_products(store_id, department.retailer_department_id)
        for raw in response.get("products", []):
            yield ProductRef(
                retailer_product_id=str(raw["itemId"]),
                name=raw["name"],
                department=department,
                upc=raw.get("upc"),
                image_url=raw.get("imageUrl"),
            )

    def check_price(
        self, browser_ctx: Any, product_ref: ProductRef, store: StoreInfo
    ) -> PriceObservation:
        client = HomeDepotApiClient(browser_ctx)
        raw = client.product_price(store.retailer_store_id, product_ref.retailer_product_id)

        clearance_signal = self.detect_clearance(raw)
        aisle, bay = self.location_hint(browser_ctx, product_ref, store)

        observation = PriceObservation(
            product_ref=product_ref,
            store=store,
            observed_at=datetime.now(timezone.utc),
            price_cents=int(round(float(raw["price"]) * 100)),
            list_price_cents=(
                int(round(float(raw["listPrice"]) * 100)) if raw.get("listPrice") else None
            ),
            is_clearance=bool(clearance_signal and clearance_signal.is_clearance),
            fulfillment_state=raw.get("fulfillmentState"),
            aisle=aisle,
            bay=bay,
            raw_signal=raw,
        )
        # is_penny depends on the fully-built observation (price + fulfillment
        # state together), so it's computed after construction and the
        # dataclass is frozen — rebuild with the flag set.
        return PriceObservation(
            **{**observation.__dict__, "is_penny": self.detect_penny(observation)}
        )

    def detect_clearance(self, raw_response: dict[str, Any]):
        return _detect_clearance(raw_response)

    def detect_penny(self, observation: PriceObservation) -> bool:
        return _detect_penny(observation)

    def location_hint(
        self, browser_ctx: Any, product_ref: ProductRef, store: StoreInfo
    ) -> tuple[str | None, str | None]:
        # HD's product_price response may include aisle/bay directly —
        # confirm from a captured response and read it here instead of a
        # second request, if so.
        return (None, None)

    def rate_limit_policy(self) -> RateLimitPolicy:
        # Starting point matching HDScanner's documented behavior (403 ->
        # 15 min floor, several-hour ceiling). Tune min/max delay once you
        # have real-world 403 rates to react to.
        return RateLimitPolicy(
            min_delay_seconds=2.0,
            max_delay_seconds=6.0,
            backoff_on_403_seconds=15 * 60,
            max_backoff_seconds=6 * 60 * 60,
        )

    def _require_store_id(self, browser_ctx: Any) -> str:
        store_id = getattr(browser_ctx, "clearance_scout_store_id", None)
        if not store_id:
            raise RuntimeError(
                "No store selected on this browser context — call select_store() first."
            )
        return store_id
