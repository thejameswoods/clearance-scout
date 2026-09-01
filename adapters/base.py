"""Retailer adapter contract.

The orchestrator, the DB layer, the web backend, and the Telegram bot all
speak only in these dataclasses and this ABC. A retailer-specific package
(e.g. `adapters/home_depot/`) implements `RetailerAdapter` and registers
itself in `adapters/registry.py` — nothing outside that package should ever
touch a retailer's raw JSON response.

Adding a new retailer means: write an adapter, add one line to the registry.
No changes to orchestrator.py, the DB schema, the web backend, or the bot.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator


class NeedsLogin(Exception):
    """Raised by authenticate() when the persistent browser session is no
    longer valid. Never caught-and-silently-retried by the orchestrator —
    it must surface to the dashboard and the Telegram bot so a human logs
    back in via noVNC."""


@dataclass(frozen=True)
class StoreInfo:
    retailer_store_id: str
    zip_code: str
    name: str | None = None
    address: str | None = None
    distance_miles: float | None = None


@dataclass(frozen=True)
class Department:
    retailer_department_id: str
    name: str
    parent_department_id: str | None = None


@dataclass(frozen=True)
class ProductRef:
    """A lightweight handle collected during the department-listing phase.
    Enough to re-look-up the product for a price check — not the full
    product record, which the orchestrator persists separately."""
    retailer_product_id: str
    name: str
    department: Department
    upc: str | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class ClearanceSignal:
    """A retailer's own "this is marked down" signal (HD's yellow tag, or
    whatever the equivalent is elsewhere). Kept separate from PriceObservation
    so detect_clearance() can be unit-tested against raw fixture JSON alone."""
    is_clearance: bool
    reason: str | None = None       # e.g. "yellow_tag", "advertised_markdown"


@dataclass(frozen=True)
class PriceObservation:
    product_ref: ProductRef
    store: StoreInfo
    observed_at: datetime
    price_cents: int
    list_price_cents: int | None = None
    is_clearance: bool = False
    is_penny: bool = False
    fulfillment_state: str | None = None
    stock_quantity: int | None = None
    aisle: str | None = None
    bay: str | None = None
    canonical_url: str | None = None
    image_url: str | None = None
    raw_signal: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PriceCheckResult:
    """One item's outcome from check_prices() -- a result wrapper, not a
    raised exception, so a batch call can report a per-item failure
    without the whole batch/wave dying because one item failed."""
    product_ref: ProductRef
    observation: PriceObservation | None = None
    error: str | None = None  # None means success


@dataclass(frozen=True)
class AuthResult:
    valid: bool
    detail: str | None = None


@dataclass(frozen=True)
class RateLimitPolicy:
    """An adapter DECLARES its own pacing; scanner/ratelimit.py ENFORCES it
    generically. A future adapter just declares different numbers — the
    engine doesn't change."""
    min_delay_seconds: float
    max_delay_seconds: float
    backoff_on_403_seconds: float = 15 * 60          # HDScanner's documented floor
    max_backoff_seconds: float = 6 * 60 * 60          # HDScanner's documented ceiling


class RetailerAdapter(ABC):
    """One instance per retailer. All browser interaction goes through the
    live Playwright BrowserContext passed in — never a bare HTTP client —
    so requests share the real session's cookies, headers, and TLS/browser
    fingerprint. See adapters/README.md for the full contract writeup."""

    retailer_slug: str

    @abstractmethod
    def authenticate(self, browser_ctx: Any) -> AuthResult:
        """Verify the persistent context still has a valid session.
        Raise NeedsLogin if not — never auto-retry a login internally."""

    @abstractmethod
    def find_stores(
        self, browser_ctx: Any, zip_code: str, radius_miles: float
    ) -> Iterator[StoreInfo]:
        """Resolve every store within radius_miles of a ZIP code — not just
        the single nearest one. Watching "any clearance wire within 25
        miles" means scanning every store in range, not picking one."""

    @abstractmethod
    def select_store(self, browser_ctx: Any, store: StoreInfo) -> None:
        """Make `store` the active store for subsequent discover_departments
        / list_products / check_price calls on this browser context. Split
        from find_stores so the orchestrator can loop: find once, select+
        scan once per store."""

    @abstractmethod
    def discover_departments(self, browser_ctx: Any) -> Iterator[Department]:
        """Phase 1: map the retailer's department/category structure."""

    @abstractmethod
    def list_products(
        self, browser_ctx: Any, department: Department
    ) -> Iterator[ProductRef]:
        """Phase 2: enumerate products in a department. The orchestrator is
        responsible for caching these into the `product` table — this
        method should not skip products just because they were seen before;
        that decision belongs to the orchestrator, not the adapter."""

    @abstractmethod
    def check_price(
        self, browser_ctx: Any, product_ref: ProductRef, store: StoreInfo
    ) -> PriceObservation:
        """Phase 3: a single product's current price/clearance/penny state
        at one store."""

    def check_prices(
        self, browser_ctx: Any, product_refs: list[ProductRef], store: StoreInfo,
        rate_limiter: Any,
    ) -> Iterator[PriceCheckResult]:
        """Batch phase 3. Default: one check_price() call per product,
        paced by `rate_limiter` (a scanner.ratelimit.RateLimiter -- typed
        Any here to avoid a circular import, same reason browser_ctx is
        Any). This preserves the original per-item behavior exactly, so an
        adapter that doesn't override this keeps working unchanged.

        Override this for real batching (see HomeDepotAdapter, modeled on
        HDScanner's own validated wave approach: concurrent batched
        requests instead of one item at a time, confirmed ~90x faster in
        practice). Pacing for an override becomes the adapter's own
        concern rather than living in this generic default -- the
        validated batch size, concurrency, and backoff cadence are all
        retailer-specific numbers, not a generic formula.
        """
        for ref in product_refs:
            rate_limiter.wait_before_next_request()
            try:
                observation = self.check_price(browser_ctx, ref, store)
                rate_limiter.record_success()
                yield PriceCheckResult(product_ref=ref, observation=observation)
            except PermissionError as exc:
                rate_limiter.record_403()
                yield PriceCheckResult(product_ref=ref, error=str(exc))
            except Exception as exc:
                yield PriceCheckResult(product_ref=ref, error=str(exc))

    @abstractmethod
    def detect_clearance(self, raw_response: dict[str, Any]) -> ClearanceSignal | None:
        """Parse a retailer's own markdown signal out of a raw API
        response. Isolated on purpose so it's unit-testable against fixture
        JSON with no browser involved."""

    @abstractmethod
    def detect_penny(self, observation: PriceObservation) -> bool:
        """Price + fulfillment-state fingerprint for $0.01-style clearance."""

    def location_hint(
        self, browser_ctx: Any, product_ref: ProductRef, store: StoreInfo
    ) -> tuple[str | None, str | None]:
        """Aisle/bay if the retailer's API exposes it. Default: not
        available. Returns (aisle, bay)."""
        return (None, None)

    def enrich_batch(
        self, browser_ctx: Any, store: StoreInfo, product_refs: list[ProductRef],
    ) -> dict[str, dict[str, Any]]:
        """Best-effort canonical_url/image_url/aisle/bay lookup for a batch
        of already-known products at one store, independent of current
        clearance/penny status -- unlike check_prices()'s enrichment (only
        a confirmed hit gets enriched, to avoid an API call per product
        checked), this backs the on-demand "repair missing data" tool, so
        it deliberately ignores that gate. Only product_ref.retailer_product_id
        is used -- other ProductRef fields exist for adapter-contract
        consistency, not because this needs them. Returns
        {retailer_product_id: {"canonical_url", "image_url", "aisle", "bay"}}
        -- a product_id absent from the result means enrichment failed for
        it (caller treats that as "still missing", not an error). Default:
        not supported."""
        return {}

    @abstractmethod
    def rate_limit_policy(self) -> RateLimitPolicy:
        """This adapter's own pacing/backoff numbers."""
