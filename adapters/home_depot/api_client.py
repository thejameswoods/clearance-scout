"""Home Depot's real internal API — a GraphQL federation gateway, not a
REST API as originally guessed. Confirmed by reading the public source of
HDScanner (github.com/apedonkey/hdscanner, no license — the queries/
endpoints below are facts about how Home Depot's own API works, learned
from their code, not a copy of their implementation; this file is an
independent rewrite).

All calls are POSTs to GRAPHQL_URL with `?opname=<operation>` and a JSON
body of `{operationName, variables, query}`.

Transport: routed through browser-extension/home_depot/'s content script
(original code, not a fork of anything -- see that directory), not
`context.request` or a bare in-page `fetch()` via page.evaluate(). Both of
those were tried first and both got a generic degraded response from Home
Depot's real API even using these exact confirmed-correct queries (see
docs/architecture.md for the full debugging trail) -- a real, installed
content script is a different execution context than either, and is what
this now uses. `_graphql()` bridges to it via `window.postMessage` (see
content.js): Playwright's `page.evaluate()` runs in the page's main world,
content scripts run in an isolated world, and postMessage is the standard
way to cross that boundary.

Home Depot's own bot-management layer for this API is Akamai (a 403/429
here is Akamai, not PerimeterX — PerimeterX guards the login flow
specifically, which this project no longer touches; see
docs/architecture.md). Confirmed safe pacing from the same source:
16 itemIds max per mediaPriceInventory call, small (300-500ms) pauses
between batches, conservative parallelism (2-5 concurrent requests).
"""

from __future__ import annotations

import json
import secrets
from typing import Any

HOME_URL = "https://www.homedepot.com/"
BRIDGE_TIMEOUT_MS = 20000

GRAPHQL_URL = "https://apionline.homedepot.com/federation-gateway/graphql"
# Headers are set inside content.js now, not here -- the content script
# performs the actual fetch(), this module only builds the request body.

# Home Depot's own documented max for a single mediaPriceInventory call.
PRICE_CHECK_MAX_BATCH = 16

STORE_SEARCH_QUERY = """
query storeSearch($lat: String, $lng: String, $storeSearchInput: String, $pagesize: String, $storeFeaturesFilter: StoreFeaturesFilter) {
  storeSearch(
    lat: $lat
    lng: $lng
    storeSearchInput: $storeSearchInput
    pagesize: $pagesize
    storeFeaturesFilter: $storeFeaturesFilter
  ) {
    stores {
      storeId
      name
      address { street city state postalCode country }
      coordinates { lat lng }
      distance
      phone
      storeType
    }
  }
}"""

# Category product listing -- returns item IDs for a department (navParam),
# paginated. `storefilter: IN_STORE` matches HDScanner's default (only
# products actually stocked somewhere), `orderBy` matches their default
# (TOP_SELLERS) since it doesn't affect which items exist, just the order
# pages are walked in.
#
# HDScanner's own version of this query requests only `itemId` -- they
# don't need a name at this stage (their keyword/department narrowing
# happens differently than ours). WATCH_KEYWORDS filters ProductRefs by
# name *before* any price check (see scanner/orchestrator.py) specifically
# to keep the request footprint small, which only works if a real name is
# available this early. `identifiers { productLabel }` below is an
# UNVERIFIED addition, not confirmed from HDScanner's source -- an
# educated guess that `searchModel.products` shares the same underlying
# Product type as the standalone `product(itemId)` query (which does have
# `identifiers.productLabel`, see PRODUCT_QUERY). Confirm this works on
# first real deploy; if the field doesn't exist on this type, this query
# will fail loudly (a normal GraphQL error, not a silent bad match) and
# needs a different fix -- e.g. a cheap batched name-only lookup before
# price-checking, rather than assuming the fields overlap.
CATEGORY_QUERY = """
query searchModel(
  $storeId: String, $navParam: String, $storefilter: StoreFilter,
  $channel: Channel, $additionalSearchParams: AdditionalParams,
  $isBrandPricingPolicyCompliant: Boolean,
  $orderBy: ProductSort, $ps: Int, $si: Int
) {
  searchModel(
    navParam: $navParam, storeId: $storeId, storefilter: $storefilter,
    channel: $channel, additionalSearchParams: $additionalSearchParams,
    isBrandPricingPolicyCompliant: $isBrandPricingPolicyCompliant
  ) {
    metadata { productCount { inStore } }
    products(pageSize: $ps, startIndex: $si, orderBy: $orderBy) {
      itemId
      identifiers { productLabel }
    }
  }
}"""

# Price/clearance/fulfillment for up to PRICE_CHECK_MAX_BATCH items at once.
MEDIA_PRICE_INVENTORY_QUERY = """
query mediaPriceInventory($itemIds: [String!]!, $storeId: String!) {
  products(itemIds: $itemIds) {
    itemId
    pricing(storeId: $storeId) { value original clearance { value dollarOff percentageOff } }
    fulfillment(storeId: $storeId) { fulfillmentOptions { type fulfillable services { type locations { inventory { quantity isInStock } locationId } } } }
  }
}"""

# Full product record (name, brand, canonical URL) -- only needed for
# confirmed clearance/penny hits, not every product checked.
PRODUCT_QUERY = """
query productClientOnlyProduct($itemId: String!, $storeId: String!) {
  product(itemId: $itemId) {
    itemId
    identifiers { productLabel brandName canonicalUrl modelNumber storeSkuNumber }
    pricing(storeId: $storeId) { value original clearance { value dollarOff percentageOff } }
  }
}"""


class HomeDepotApiClient:
    """Issues GraphQL calls through browser-extension/home_depot/'s content
    script (see this module's docstring for why), bridged via
    `window.postMessage` from a real homedepot.com page."""

    def __init__(self, browser_ctx: Any):
        self._ctx = browser_ctx
        self._page = self._get_or_create_page()

    def _get_or_create_page(self) -> Any:
        # Reuse an existing homedepot.com tab if one's already open (the
        # content script is only injected on matching pages) rather than
        # opening a new one per HomeDepotApiClient instantiation -- matches
        # how a real user has exactly one tab open, not several.
        for page in self._ctx.pages:
            if page.url and "homedepot.com" in page.url:
                return page
        page = self._ctx.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        return page

    def _graphql(self, operation: str, variables: dict[str, Any], query: str) -> dict[str, Any]:
        request_id = secrets.token_hex(8)
        body = {"operationName": operation, "variables": variables, "query": query}
        result = self._page.evaluate(
            """
            ({ requestId, url, body, timeoutMs }) => new Promise((resolve) => {
                function handler(event) {
                    if (event.source !== window) return;
                    const msg = event.data;
                    if (!msg || msg.type !== "CS_SCOUT_GRAPHQL_RESPONSE" || msg.requestId !== requestId) return;
                    window.removeEventListener("message", handler);
                    clearTimeout(timer);
                    resolve(msg);
                }
                const timer = setTimeout(() => {
                    window.removeEventListener("message", handler);
                    resolve({ status: 0, body: null, error: "bridge timeout -- is the extension loaded?" });
                }, timeoutMs);
                window.addEventListener("message", handler);
                window.postMessage({ type: "CS_SCOUT_GRAPHQL_REQUEST", requestId, url, body }, "*");
            })
            """,
            {"requestId": request_id, "url": f"{GRAPHQL_URL}?opname={operation}", "body": body, "timeoutMs": BRIDGE_TIMEOUT_MS},
        )

        if result.get("status") == 0:
            raise RuntimeError(f"Extension bridge failed for {operation}: {result.get('error')}")
        if result["status"] in (403, 429):
            raise PermissionError(f"Home Depot (Akamai) returned {result['status']} for {operation}")
        try:
            return json.loads(result["body"])
        except ValueError as exc:
            raise RuntimeError(
                f"Home Depot returned a non-JSON response for {operation} (status {result['status']})"
            ) from exc

    def store_search(self, zip_code: str, radius_miles: float) -> dict[str, Any]:
        # radius_miles isn't a real storeSearch param (HDScanner doesn't use
        # one either -- pagesize=20 is the only result-count control) --
        # kept in the adapter signature for the generic contract, applied
        # as a post-filter on `distance` in the adapter instead.
        return self._graphql(
            "storeSearch",
            {
                "lat": "", "lng": "", "pagesize": "20",
                "storeSearchInput": zip_code, "storeFeaturesFilter": {},
            },
            STORE_SEARCH_QUERY,
        )

    def category_products(
        self, store_id: str, nav_param: str, page_size: int = 48, start_index: int = 0
    ) -> dict[str, Any]:
        return self._graphql(
            "searchModel",
            {
                "storeId": store_id, "navParam": nav_param,
                "storefilter": "IN_STORE", "channel": "DESKTOP",
                "isBrandPricingPolicyCompliant": False,
                "additionalSearchParams": {"multiStoreIds": []},
                "orderBy": {"field": "TOP_SELLERS", "order": "ASC"},
                "ps": page_size, "si": start_index,
            },
            CATEGORY_QUERY,
        )

    def media_price_inventory(self, store_id: str, item_ids: list[str]) -> dict[str, Any]:
        if len(item_ids) > PRICE_CHECK_MAX_BATCH:
            raise ValueError(f"media_price_inventory: max {PRICE_CHECK_MAX_BATCH} itemIds per call")
        return self._graphql(
            "mediaPriceInventory",
            {"itemIds": item_ids, "storeId": store_id},
            MEDIA_PRICE_INVENTORY_QUERY,
        )

    def product_detail(self, store_id: str, item_id: str) -> dict[str, Any]:
        return self._graphql(
            "productClientOnlyProduct",
            {"itemId": item_id, "storeId": store_id},
            PRODUCT_QUERY,
        )
