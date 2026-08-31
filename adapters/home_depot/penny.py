"""Penny-item detection: price + fulfillment-state fingerprint.

Confirmed real signal (from reading HDScanner's source, see api_client.py's
module docstring): a clearance price of exactly $0.01. HDScanner layers
additional heuristics on top (an "anchor store status" check via a second,
richer per-product query, to distinguish a true penny item from a data
glitch or an out-of-stock placeholder) -- that refinement is deliberately
not replicated here yet. This is a known simplification: price + basic
in-stock fulfillment is a reasonable v1 signal, not the full picture.
Revisit if false positives show up in practice.
"""

from __future__ import annotations

from ..base import PriceObservation

PENNY_PRICE_CENTS = 1


def detect_penny(observation: PriceObservation) -> bool:
    if observation.price_cents != PENNY_PRICE_CENTS:
        return False
    return observation.fulfillment_state == "in_stock"
