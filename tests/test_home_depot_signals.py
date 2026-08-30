"""Pure-function tests for the Home Depot adapter's signal parsing — no
browser, no network, no DB. See adapters/home_depot/clearance.py and
penny.py for why the exact field names here are placeholders pending real
captured traffic (adapters/home_depot/api_client.py's module docstring)."""

from __future__ import annotations

from adapters.base import PriceObservation, ProductRef, Department, StoreInfo
from adapters.home_depot.clearance import detect_clearance
from adapters.home_depot.penny import detect_penny
from datetime import datetime, timezone


def test_detect_clearance_from_badge():
    signal = detect_clearance({"badge": "Clearance"})
    assert signal is not None
    assert signal.is_clearance is True


def test_detect_clearance_from_price_type():
    signal = detect_clearance({"priceType": "clearance"})
    assert signal is not None


def test_detect_clearance_none_for_regular_price():
    assert detect_clearance({"badge": "", "priceType": "regular"}) is None


def _observation(price_cents: int, fulfillment_state: str | None) -> PriceObservation:
    dept = Department(retailer_department_id="1", name="Test")
    ref = ProductRef(retailer_product_id="sku", name="Test Product", department=dept)
    store = StoreInfo(retailer_store_id="1", zip_code="00000")
    return PriceObservation(
        product_ref=ref, store=store, observed_at=datetime.now(timezone.utc),
        price_cents=price_cents, fulfillment_state=fulfillment_state,
    )


def test_detect_penny_true_for_penny_price_and_valid_state():
    assert detect_penny(_observation(1, "in_stock")) is True


def test_detect_penny_false_for_non_penny_price():
    assert detect_penny(_observation(100, "in_stock")) is False


def test_detect_penny_false_without_fulfillment_state():
    assert detect_penny(_observation(1, None)) is False
