from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

from ..base import (
    AuthResult,
    Department,
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

# HDScanner's own validated pagination limits for category listing (see
# api_client.py's module docstring for how these were confirmed).
CATEGORY_PAGE_SIZE = 48
CATEGORY_MAX_PAGES = 5  # 5*48=240 items/department -- more conservative than
                         # HDScanner's 15-page/720-item cap; WATCHED_DEPARTMENTS
                         # already narrows scope, so exhaustiveness matters less.


class HomeDepotAdapter(RetailerAdapter):
    retailer_slug = "home_depot"

    def authenticate(self, browser_ctx: Any) -> AuthResult:
        # Home Depot doesn't require a logged-in session to browse
        # products/prices/clearance status by store -- login only matters
        # for account-specific things (order history, Pro pricing, saved
        # lists) that this scanner doesn't touch. So: never gate scanning
        # on login here. This also means never navigating to /myaccount/
        # during a normal scan -- that's the account/auth surface, which
        # is reasonably the most heavily monitored part of the site for
        # fraud/bot signals, and there's no reason to poke it when nothing
        # downstream needs it.
        #
        # Login is tabled for now (2026-08-30): the real login flow itself
        # 403's against Home Depot's actual auth API even with Patchright's
        # CDP-level fingerprint patches, which is a harder problem than
        # this project needs to solve to find clearance deals. If a future
        # need reintroduces a login requirement (e.g. a retailer where
        # pricing genuinely requires an account), reintroduce a real check
        # here rather than assuming this pattern generalizes.
        return AuthResult(valid=True)

    def find_stores(
        self, browser_ctx: Any, zip_code: str, radius_miles: float
    ) -> Iterator[StoreInfo]:
        client = HomeDepotApiClient(browser_ctx)
        response = client.store_search(zip_code, radius_miles)
        stores = response.get("data", {}).get("storeSearch", {}).get("stores") or []
        for raw in stores:
            distance = raw.get("distance")
            # storeSearch has no radius param of its own (confirmed --
            # HDScanner doesn't use one either); apply it client-side.
            if distance is not None and distance > radius_miles:
                continue
            address_parts = raw.get("address") or {}
            address = ", ".join(
                p for p in (
                    address_parts.get("street"),
                    address_parts.get("city"),
                    address_parts.get("state"),
                    address_parts.get("postalCode"),
                ) if p
            ) or None
            yield StoreInfo(
                retailer_store_id=str(raw["storeId"]),
                zip_code=zip_code,
                name=raw.get("name"),
                address=address,
                distance_miles=distance,
            )

    def select_store(self, browser_ctx: Any, store: StoreInfo) -> None:
        # Confirmed: storeId is just a per-request GraphQL variable, not
        # site-side state (no cookie/page interaction needed to "select" a
        # store) -- see api_client.py. Recording it on the context is all
        # _require_store_id() needs.
        browser_ctx.clearance_scout_store_id = store.retailer_store_id

    def discover_departments(self, browser_ctx: Any) -> Iterator[Department]:
        yield from _discover_departments(browser_ctx)

    def list_products(
        self, browser_ctx: Any, department: Department
    ) -> Iterator[ProductRef]:
        client = HomeDepotApiClient(browser_ctx)
        store_id = self._require_store_id(browser_ctx)

        seen_item_ids: set[str] = set()
        for page in range(CATEGORY_MAX_PAGES):
            response = client.category_products(
                store_id, department.retailer_department_id,
                page_size=CATEGORY_PAGE_SIZE, start_index=page * CATEGORY_PAGE_SIZE,
            )
            search_model = response.get("data", {}).get("searchModel") or {}
            products = search_model.get("products") or []
            if not products:
                break
            for raw in products:
                item_id = raw.get("itemId")
                if not item_id or item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)
                # See CATEGORY_QUERY's docstring in api_client.py: the name
                # field here is an unverified addition to HDScanner's
                # original query. Falling back to the item_id keeps this
                # from crashing if it's missing -- but WATCH_KEYWORDS can't
                # actually filter anything meaningful without a real name,
                # so treat an all-item_id product list as a signal this
                # needs the fallback lookup mentioned in that docstring.
                name = (raw.get("identifiers") or {}).get("productLabel") or str(item_id)
                yield ProductRef(
                    retailer_product_id=str(item_id),
                    name=name,
                    department=department,
                )
            if len(products) < CATEGORY_PAGE_SIZE:
                break

    def check_price(
        self, browser_ctx: Any, product_ref: ProductRef, store: StoreInfo
    ) -> PriceObservation:
        # NOTE: Home Depot's real API supports up to 16 itemIds per
        # mediaPriceInventory call (see api_client.py) -- HDScanner batches
        # aggressively for exactly this reason. This adapter currently
        # calls it one item at a time because RetailerAdapter.check_price()
        # is a per-product method (see adapters/base.py). That's up to 16x
        # more requests than necessary and a real known inefficiency, not
        # fixed here -- batching would need a contract change (a
        # check_prices(product_refs) -> Iterator[PriceObservation] method,
        # or similar) touching base.py and orchestrator.py, deliberately
        # deferred rather than rushed alongside everything else in this
        # session.
        client = HomeDepotApiClient(browser_ctx)
        response = client.media_price_inventory(store.retailer_store_id, [product_ref.retailer_product_id])
        products = response.get("data", {}).get("products") or []
        if not products:
            raise RuntimeError(
                f"Home Depot returned no data for item {product_ref.retailer_product_id} "
                "(delisted, invalid ID, or not carried at this store)"
            )
        raw = products[0]

        clearance_signal = self.detect_clearance(raw)
        aisle, bay = self.location_hint(browser_ctx, product_ref, store)
        pricing = raw.get("pricing") or {}
        fulfillment_state = self._pickup_fulfillment_state(raw)

        observation = PriceObservation(
            product_ref=product_ref,
            store=store,
            observed_at=datetime.now(timezone.utc),
            price_cents=int(round(float(pricing["value"]) * 100)),
            list_price_cents=(
                int(round(float(pricing["original"]) * 100)) if pricing.get("original") else None
            ),
            is_clearance=bool(clearance_signal and clearance_signal.is_clearance),
            fulfillment_state=fulfillment_state,
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
        # A real `aislebay` GraphQL query exists (confirmed alongside
        # everything else in api_client.py's module docstring) but takes
        # storeSkuIds, a different ID namespace than the itemIds used
        # everywhere else here -- wiring it in is a real follow-up, not
        # done now to keep this change reviewable.
        return (None, None)

    def rate_limit_policy(self) -> RateLimitPolicy:
        # Confirmed real values (not a guess from HDScanner's README
        # prose, which was more conservative than what their actual code
        # does) -- see api_client.py's module docstring. Their 403/429
        # backoff is 10s/30s/90s exponential + jitter (Akamai, not
        # PerimeterX -- this API layer isn't the login flow). min/max
        # delay here is more conservative than their ~300-500ms
        # between-wave pacing on purpose: they pace between waves of 16
        # batched items; this adapter calls one item at a time (see
        # check_price's docstring note), so matching their per-wave delay
        # here would mean far more total request volume in the same
        # window than their validated-safe pattern.
        return RateLimitPolicy(
            min_delay_seconds=1.5,
            max_delay_seconds=3.0,
            backoff_on_403_seconds=10,
            max_backoff_seconds=90,
        )

    def _pickup_fulfillment_state(self, raw_product: dict[str, Any]) -> str | None:
        for option in raw_product.get("fulfillment", {}).get("fulfillmentOptions", []) or []:
            if option.get("type") != "pickup":
                continue
            for service in option.get("services", []) or []:
                for location in service.get("locations", []) or []:
                    inventory = location.get("inventory") or {}
                    if inventory.get("isInStock"):
                        return "in_stock"
        return None

    def _require_store_id(self, browser_ctx: Any) -> str:
        store_id = getattr(browser_ctx, "clearance_scout_store_id", None)
        if not store_id:
            raise RuntimeError(
                "No store selected on this browser context — call select_store() first."
            )
        return store_id
