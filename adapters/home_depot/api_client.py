"""Home Depot's real internal API — a GraphQL federation gateway, not a
REST API as originally guessed. Confirmed by reading the public source of
HDScanner (github.com/apedonkey/hdscanner, no license — the queries/
endpoints below are facts about how Home Depot's own API works, learned
from their code, not a copy of their implementation; this file is an
independent rewrite).

All calls are POSTs to GRAPHQL_URL with `?opname=<operation>` and a JSON
body of `{operationName, variables, query}`.

Transport: a direct in-page `fetch()` via `page.evaluate()`, not routed
through browser-extension/home_depot/'s content script. That extension
bridge was built and tested specifically to rule execution-context in or
out as the variable behind Home Depot's generic degraded API response (see
docs/architecture.md) -- the result was identical (same degraded response)
via `context.request`, in-page `fetch()`, *and* a genuinely-loaded content
script under vanilla Playwright, which rules execution context out
entirely. Since the extension buys nothing functionally and Patchright
silently drops `--load-extension`/`--disable-extensions-except` (its own
anti-detection arg sanitization strips them, confirmed via isolated
diagnostic), routing through it just adds a dead bridge that times out
every call. `_graphql()` now does the fetch directly in `page.evaluate()`,
using the same headers confirmed from content.js.

Home Depot's own bot-management layer for this API is Akamai (a 403/429
here is Akamai, not PerimeterX — PerimeterX guards the login flow
specifically, which this project no longer touches; see
docs/architecture.md). Confirmed safe pacing from the same source:
16 itemIds max per mediaPriceInventory call, small (300-500ms) pauses
between batches, conservative parallelism (2-5 concurrent requests).
"""

from __future__ import annotations

import json
from typing import Any

HOME_URL = "https://www.homedepot.com/"

GRAPHQL_URL = "https://apionline.homedepot.com/federation-gateway/graphql"

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
#
# `media { images { url } }` had no source to confirm against -- HDScanner
# doesn't request any image field anywhere in its own source -- so it was
# added speculatively and verified live 2026-08-31 (a real, working field,
# real URL returned; see adapters/home_depot/adapter.py's
# _enrich_confirmed_hit for the "<SIZE>" template substitution the URL
# itself needs).
PRODUCT_QUERY = """
query productClientOnlyProduct($itemId: String!, $storeId: String!) {
  product(itemId: $itemId) {
    itemId
    identifiers { productLabel brandName canonicalUrl modelNumber storeSkuNumber }
    pricing(storeId: $storeId) { value original clearance { value dollarOff percentageOff } }
    media { images { url } }
  }
}"""

# Real, confirmed query (HDScanner's own `aislebay` field) -- separate from
# `product.fulfillment`, takes storeSkuNumbers (not itemIds). Batchable up
# to 20 SKUs/call (HDScanner's own chunk size); this adapter calls it with
# a single SKU per confirmed hit, same "known inefficiency, not batched
# here" posture as media_price_inventory (see check_price's docstring).
AISLEBAY_QUERY = """
query aislebay($storeId: String!, $storeSkuIds: [String!]!) {
  aislebay(storeId: $storeId, storeSkuIds: $storeSkuIds) {
    storeSkus {
      storeNumber
      storeSkuId
      aisleBayInfo { aisle bay invLocDesc invLocDescFriendly }
    }
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
        body = {"operationName": operation, "variables": variables, "query": query}
        # Headers confirmed from HDScanner's own content-script fetch() call
        # (see this module's docstring) -- reproduced directly here now that
        # the extension bridge that used to send them is gone.
        result = self._page.evaluate(
            """
            ({ url, body }) => fetch(url, {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json",
                    "x-experience-name": "general-merchandise",
                },
                body: JSON.stringify(body),
            }).then(async (response) => ({ status: response.status, body: await response.text() }))
              .catch((e) => ({ status: 0, body: null, error: String(e) }))
            """,
            {"url": f"{GRAPHQL_URL}?opname={operation}", "body": body},
        )

        if result.get("status") == 0:
            raise RuntimeError(f"Fetch failed for {operation}: {result.get('error')}")
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

    def aislebay(self, store_id: str, store_sku_ids: list[str]) -> dict[str, Any]:
        if len(store_sku_ids) > 20:
            raise ValueError("aislebay: max 20 storeSkuIds per call (HDScanner's own chunk size)")
        return self._graphql(
            "aislebay",
            {"storeId": store_id, "storeSkuIds": store_sku_ids},
            AISLEBAY_QUERY,
        )
