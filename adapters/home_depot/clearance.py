"""'Yellow tag' clearance-signal parsing.

Pure function, no browser/network involved, so it's directly unit-testable
against captured (and hand-scrubbed) fixture JSON. The field names below
(`badge`, `priceType`) are placeholders — replace them once you've captured
a real product_price response via api_client.py's noVNC capture step, then
add a fixture under tests/fixtures/home_depot/ and a test asserting this
function reads it correctly.
"""

from __future__ import annotations

from typing import Any

from ..base import ClearanceSignal

CLEARANCE_BADGE_VALUES = {"clearance", "yellow_tag", "special_buy_clearance"}


def detect_clearance(raw_response: dict[str, Any]) -> ClearanceSignal | None:
    badge = str(raw_response.get("badge", "")).strip().lower()
    price_type = str(raw_response.get("priceType", "")).strip().lower()

    if badge in CLEARANCE_BADGE_VALUES:
        return ClearanceSignal(is_clearance=True, reason=f"badge:{badge}")
    if price_type == "clearance":
        return ClearanceSignal(is_clearance=True, reason="priceType:clearance")

    return None
