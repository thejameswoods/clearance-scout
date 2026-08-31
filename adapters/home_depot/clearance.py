"""'Yellow tag' clearance-signal parsing.

Real shape confirmed by reading HDScanner's source (see api_client.py's
module docstring) -- `raw_product` here is one element of a
mediaPriceInventory response's `products[]` array:

    {"itemId": "...", "pricing": {"value": 12.34, "original": 24.99,
     "clearance": {"value": 9.97, "dollarOff": 15.02, "percentageOff": 60}},
     "fulfillment": {"fulfillmentOptions": [...]}}

Two independent signals, both derived from that shape, no separate "badge"
field:
- "Advertised" (yellow tag, vs. an unadvertised/quiet markdown): the
  product has BOPIS (buy-online-pickup-in-store) as a fulfillment option
  but pickup currently isn't fulfillable there -- HDScanner's own
  reasoning is that in-store-only clearance markdowns get pulled from
  online reservation. Kept as a pure function over the fulfillment shape
  so it's unit-testable without a browser.
- Clearance at all: `pricing.clearance` is non-null with a `.value`, AND
  either (a) the product is advertised (see above -- that fulfillment
  signal is itself independent confirmation of a real in-store markdown,
  confirmed live 2026-08-31 on SKUs 331978757 and 304093235: both had
  `pricing.value` still showing the pre-markdown price even though the
  clearance was genuinely live -- Home Depot's own API just doesn't always
  surface the in-store price online), or (b) the charged price
  (`pricing.value`) actually matches `clearance.value` directly. Without
  the advertised signal, a clearance object alone isn't enough -- confirmed
  live 2026-08-31, SKU 303289146: a non-null `pricing.clearance` that was
  stale/inapplicable (pickup still fulfillable, no BOPIS pulled), where
  the item's own product page showed no clearance tag at all.
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
    pricing = raw_response.get("pricing", {})
    clearance = pricing.get("clearance")
    if not clearance or clearance.get("value") is None:
        return None

    advertised = _is_advertised(raw_response)
    if not advertised:
        charged_price = pricing.get("value")
        if charged_price is None or round(charged_price, 2) != round(clearance["value"], 2):
            return None

    reason = "advertised_yellow_tag" if advertised else "unadvertised_clearance"
    return ClearanceSignal(is_clearance=True, reason=reason)


def effective_price(pricing: dict[str, Any], is_clearance: bool) -> tuple[float, float | None]:
    """What's actually charged, and the "was" price to show a discount
    against (or None if there isn't one). Confirmed live 2026-08-31 (SKUs
    331978757, 304093235): when clearance is confirmed advertised,
    `pricing.value` can still be the pre-markdown price, not what's
    actually charged in-store -- use `clearance.value` instead, and treat
    the old `pricing.value` as the reference/"was" price."""
    value = pricing.get("value")
    clearance = pricing.get("clearance") or {}
    clearance_value = clearance.get("value")
    reference = pricing.get("original")

    if is_clearance and clearance_value is not None:
        if reference is None and value is not None and round(value, 2) != round(clearance_value, 2):
            reference = value
        return clearance_value, reference

    return value, reference
