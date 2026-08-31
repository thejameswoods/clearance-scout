"""'Yellow tag' clearance-signal parsing.

Real shape confirmed by reading HDScanner's source (see api_client.py's
module docstring) -- `raw_product` here is one element of a
mediaPriceInventory response's `products[]` array:

    {"itemId": "...", "pricing": {"value": 12.34, "original": 24.99,
     "clearance": {"value": 9.97, "dollarOff": 15.02, "percentageOff": 60}},
     "fulfillment": {"fulfillmentOptions": [...]}}

Two independent signals, both derived from that shape, no separate "badge"
field:
- Clearance at all: `pricing.clearance` is non-null with a `.value`.
- "Advertised" (yellow tag, vs. an unadvertised/quiet markdown): the
  product has BOPIS (buy-online-pickup-in-store) as a fulfillment option
  but pickup currently isn't fulfillable there -- HDScanner's own
  reasoning is that in-store-only clearance markdowns get pulled from
  online reservation. Kept as a pure function over the fulfillment shape
  so it's unit-testable without a browser.
"""

from __future__ import annotations

from typing import Any

from ..base import ClearanceSignal


def _is_advertised(raw_product: dict[str, Any]) -> bool:
    has_bopis = False
    pickup_fulfillable = True
    for option in raw_product.get("fulfillment", {}).get("fulfillmentOptions", []) or []:
        if option.get("type") != "pickup":
            continue
        pickup_fulfillable = bool(option.get("fulfillable", True))
        for service in option.get("services", []) or []:
            if service.get("type") == "bopis":
                has_bopis = True
    return has_bopis and not pickup_fulfillable


def detect_clearance(raw_response: dict[str, Any]) -> ClearanceSignal | None:
    clearance = raw_response.get("pricing", {}).get("clearance")
    if not clearance or clearance.get("value") is None:
        return None

    reason = "advertised_yellow_tag" if _is_advertised(raw_response) else "unadvertised_clearance"
    return ClearanceSignal(is_clearance=True, reason=reason)
