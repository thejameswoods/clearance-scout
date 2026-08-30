"""Thin wrapper around Home Depot's internal product/pricing endpoints,
called through the live authenticated browser context (not a bare HTTP
client — see adapters/README.md for why).

IMPORTANT — this file is a scaffold, not a finished integration.
Home Depot doesn't publish this API; HDScanner's README only describes its
*behavior* ("calls Home Depot's own product/pricing API from within your
browser tab"), not the actual endpoint paths, request shapes, or response
JSON. Nobody should ship fabricated endpoint guesses — a wrong-but-plausible
URL fails silently or, worse, "succeeds" against the wrong data. The real
paths/payloads have to come from capturing genuine traffic:

    1. Log into homedepot.com in the scanner container's noVNC session
       (this is the same one-time login step the deploy docs already call
       for).
    2. Open DevTools → Network → XHR/Fetch, browse a department page and a
       product page for your store, and note the request URLs, headers, and
       JSON response shape actually used.
    3. Fill in HD_ENDPOINTS below and adjust the parsing in departments.py /
       adapter.py / clearance.py / penny.py to match what you captured.

Keep the *shape* of this module (config-driven base URL + endpoint paths,
one method per phase, everything routed through `browser_ctx.request`) —
just replace the placeholder paths once you have real ones.
"""

from __future__ import annotations

import os
from typing import Any

HD_BASE_URL = os.environ.get("HOME_DEPOT_BASE_URL", "https://www.homedepot.com")

# Placeholder paths — replace with real captured endpoints (see module
# docstring). Left as named constants, not inlined, so the one place that
# needs updating after packet-capture is obvious.
HD_ENDPOINTS = {
    "store_search": "/api/store-search",       # ZIP -> nearby stores
    "department_list": "/api/departments",      # store -> department tree
    "department_products": "/api/browse",       # department -> product IDs
    "product_price": "/api/product-price",      # product + store -> price/clearance
}


class HomeDepotApiClient:
    """Issues calls via the live Playwright BrowserContext's own request API
    (`context.request`), so cookies, headers, and TLS fingerprint match a
    real browser tab. If 403 rates prove that insufficient even with a
    genuine session, fall back to in-page `page.evaluate(...fetch...)`
    instead of a bare HTTP client — see adapters/README.md."""

    def __init__(self, browser_ctx: Any):
        self._ctx = browser_ctx

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._ctx.request.get(f"{HD_BASE_URL}{path}", params=params or {})
        if response.status == 403:
            raise PermissionError(f"Home Depot returned 403 for {path}")
        response_json = response.json()
        return response_json if isinstance(response_json, dict) else {"data": response_json}

    def store_search(self, zip_code: str) -> dict[str, Any]:
        return self._get(HD_ENDPOINTS["store_search"], {"zip": zip_code})

    def department_list(self, store_id: str) -> dict[str, Any]:
        return self._get(HD_ENDPOINTS["department_list"], {"storeId": store_id})

    def department_products(self, store_id: str, department_id: str) -> dict[str, Any]:
        return self._get(
            HD_ENDPOINTS["department_products"],
            {"storeId": store_id, "departmentId": department_id},
        )

    def product_price(self, store_id: str, product_id: str) -> dict[str, Any]:
        return self._get(
            HD_ENDPOINTS["product_price"],
            {"storeId": store_id, "itemId": product_id},
        )
