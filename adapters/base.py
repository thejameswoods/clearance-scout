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
    aisle: str | None = None
    bay: str | None = None
    raw_signal: dict[str, Any] = field(default_factory=dict)


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

    @abstractmethod
    def rate_limit_policy(self) -> RateLimitPolicy:
        """This adapter's own pacing/backoff numbers."""
