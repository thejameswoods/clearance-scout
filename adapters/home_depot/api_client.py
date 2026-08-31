"""Home Depot's real internal API — a GraphQL federation gateway, not a
REST API as originally guessed. Confirmed by reading the public source of
HDScanner (github.com/apedonkey/hdscanner, no license — the queries/
endpoints below are facts about how Home Depot's own API works, learned
from their code, not a copy of their implementation; this file is an
independent rewrite).

All calls are POSTs to GRAPHQL_URL with `?opname=<operation>` and a JSON
body of `{operationName, variables, query}`, issued through the live
browser context (see adapters/README.md for why) so they carry the same
cookies/headers/session as the visible browser.

Home Depot's own bot-management layer for this API is Akamai (a 403/429
here is Akamai, not PerimeterX — PerimeterX guards the login flow
specifically, which this project no longer touches; see
docs/architecture.md). Confirmed safe pacing from the same source:
16 itemIds max per mediaPriceInventory call, small (300-500ms) pauses
between batches, conservative parallelism (2-5 concurrent requests).
"""

from __future__ import annotations

from typing import Any

GRAPHQL_URL = "https://apionline.homedepot.com/federation-gateway/graphql"
GRAPHQL_HEADERS = {
    "Content-Type": "application/json",
    "x-experience-name": "general-merchandise",
}

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
    """Issues calls via the live Playwright/Patchright BrowserContext's own
    request API (`context.request`), so cookies, headers, and TLS
    fingerprint match a real browser tab."""

    def __init__(self, browser_ctx: Any):
        self._ctx = browser_ctx

    def _graphql(self, operation: str, variables: dict[str, Any], query: str) -> dict[str, Any]:
        response = self._ctx.request.post(
            f"{GRAPHQL_URL}?opname={operation}",
            headers=GRAPHQL_HEADERS,
            data={"operationName": operation, "variables": variables, "query": query},
        )
        if response.status in (403, 429):
            raise PermissionError(f"Home Depot (Akamai) returned {response.status} for {operation}")
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Home Depot returned a non-JSON response for {operation} (status {response.status})"
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
