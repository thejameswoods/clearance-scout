"""Pure-function tests for the Home Depot adapter's signal parsing — no
browser, no network, no DB. Shapes here are confirmed real (see
adapters/home_depot/api_client.py's module docstring for how), not
guessed placeholders."""

from __future__ import annotations

from datetime import datetime, timezone

from adapters.base import Department, PriceObservation, ProductRef, StoreInfo
from adapters.home_depot.clearance import detect_clearance
from adapters.home_depot.penny import detect_penny


def _pickup_option(fulfillable: bool, has_bopis: bool) -> dict:
    services = [{"type": "bopis", "locations": []}] if has_bopis else []
    return {"type": "pickup", "fulfillable": fulfillable, "services": services}


def test_detect_clearance_advertised_yellow_tag():
    raw = {
        "pricing": {"value": 9.97, "original": 24.99, "clearance": {"value": 9.97, "dollarOff": 15.02, "percentageOff": 60}},
        "fulfillment": {"fulfillmentOptions": [_pickup_option(fulfillable=False, has_bopis=True)]},
    }
    signal = detect_clearance(raw)
    assert signal is not None
    assert signal.is_clearance is True
    assert signal.reason == "advertised_yellow_tag"


def test_detect_clearance_unadvertised_when_pickup_still_fulfillable():
    raw = {
        "pricing": {"value": 9.97, "original": 24.99, "clearance": {"value": 9.97, "dollarOff": 15.02, "percentageOff": 60}},
        "fulfillment": {"fulfillmentOptions": [_pickup_option(fulfillable=True, has_bopis=True)]},
    }
    signal = detect_clearance(raw)
    assert signal is not None
    assert signal.reason == "unadvertised_clearance"


def test_detect_clearance_none_for_regular_price():
    raw = {"pricing": {"value": 24.99, "original": 24.99, "clearance": None}, "fulfillment": {}}
    assert detect_clearance(raw) is None


def _observation(price_cents: int, fulfillment_state: str | None) -> PriceObservation:
    dept = Department(retailer_department_id="1", name="Test")
    ref = ProductRef(retailer_product_id="sku", name="Test Product", department=dept)
    store = StoreInfo(retailer_store_id="1", zip_code="00000")
    return PriceObservation(
        product_ref=ref, store=store, observed_at=datetime.now(timezone.utc),
        price_cents=price_cents, fulfillment_state=fulfillment_state,
    )


def test_detect_penny_true_for_penny_price_and_in_stock():
    assert detect_penny(_observation(1, "in_stock")) is True


def test_detect_penny_false_for_non_penny_price():
    assert detect_penny(_observation(100, "in_stock")) is False


def test_detect_penny_false_without_fulfillment_state():
    assert detect_penny(_observation(1, None)) is False
