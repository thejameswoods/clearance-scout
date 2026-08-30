"""Penny-item detection: price + fulfillment-state fingerprint.

HDScanner's README describes this as hunting $0.01 items using "a
fingerprint of price + fulfillment state" — i.e. price alone isn't a
reliable signal (misc fees, sample SKUs, etc. can also show $0.01), and the
fulfillment_state field is needed to disambiguate. Which exact
fulfillment_state values correlate with genuine penny clearance (vs. a
data glitch or an out-of-stock placeholder) can only be determined by
observing real captured responses — see api_client.py's module docstring
for the capture procedure. PENNY_FULFILLMENT_STATES below is a placeholder
starting point, not a verified list.
"""

from __future__ import annotations

from ..base import PriceObservation

PENNY_PRICE_CENTS = 1
PENNY_FULFILLMENT_STATES = {"in_stock", "limited_stock"}


def detect_penny(observation: PriceObservation) -> bool:
    if observation.price_cents != PENNY_PRICE_CENTS:
        return False
    if observation.fulfillment_state is None:
        return False
    return observation.fulfillment_state.strip().lower() in PENNY_FULFILLMENT_STATES
