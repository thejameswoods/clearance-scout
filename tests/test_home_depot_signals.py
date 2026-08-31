"""Pure-function tests for the Home Depot adapter's signal parsing — no
browser, no network, no DB. Shapes here are confirmed real (see
adapters/home_depot/api_client.py's module docstring for how), not
guessed placeholders."""

from __future__ import annotations

from datetime import datetime, timezone

from adapters.base import Department, PriceObservation, ProductRef, StoreInfo
from adapters.home_depot.clearance import detect_clearance, effective_price, stock_quantity
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


def test_detect_clearance_none_when_unadvertised_and_price_does_not_match():
    # Confirmed live 2026-08-31 (SKU 303289146): the API can return a
    # non-null pricing.clearance object even when it isn't the price
    # actually being charged -- pricing.value here is full price ($179),
    # not the $44.75 the stale clearance object claims, AND there's no
    # independent advertised-clearance signal (real fulfillment used
    # service type "boss", not "bopis", with pickup still fulfillable).
    # Flagging this as a live deal was a false positive the item's own
    # product page didn't show. Without the advertised signal, a clearance
    # object only counts as *currently applied* if it's reflected in what's
    # actually being charged.
    raw = {
        "pricing": {"value": 179.0, "original": None, "clearance": {"value": 44.75, "dollarOff": 134.25, "percentageOff": 75.0}},
        "fulfillment": {"fulfillmentOptions": [{"type": "pickup", "fulfillable": True, "services": [{"type": "boss", "locations": []}]}]},
    }
    assert detect_clearance(raw) is None


def test_detect_clearance_true_when_advertised_even_if_price_does_not_match():
    # Confirmed live 2026-08-31 (SKU 331978757): a genuine in-store yellow
    # tag -- BOPIS present but pickup not fulfillable, HDScanner's own
    # advertised-clearance signal -- while pricing.value ($10.97) still
    # doesn't reflect the marked-down price ($8.78). Unlike the unadvertised
    # case above, the independent fulfillment signal is itself enough
    # confirmation; the price mismatch here means Home Depot's own API
    # just doesn't surface the in-store price online, not that the
    # clearance isn't real.
    raw = {
        "pricing": {"value": 10.97, "original": None, "clearance": {"value": 8.78, "dollarOff": 2.19, "percentageOff": 20.0}},
        "fulfillment": {"fulfillmentOptions": [_pickup_option(fulfillable=False, has_bopis=True)]},
    }
    signal = detect_clearance(raw)
    assert signal is not None
    assert signal.reason == "advertised_yellow_tag"


def test_effective_price_uses_clearance_value_when_advertised_price_mismatches():
    # SKU 331978757: pricing.value ($10.97) never got updated to the live
    # in-store markdown ($8.78) even though it's confirmed advertised.
    pricing = {"value": 10.97, "original": None, "clearance": {"value": 8.78, "dollarOff": 2.19, "percentageOff": 20.0}}
    price, reference = effective_price(pricing, is_clearance=True)
    assert price == 8.78
    assert reference == 10.97  # the old charged price becomes the "was" price


def test_effective_price_prefers_real_original_over_inferred_reference():
    pricing = {"value": 9.97, "original": 24.99, "clearance": {"value": 9.97, "dollarOff": 15.02, "percentageOff": 60}}
    price, reference = effective_price(pricing, is_clearance=True)
    assert price == 9.97
    assert reference == 24.99


def test_effective_price_passthrough_when_not_clearance():
    pricing = {"value": 24.99, "original": None, "clearance": None}
    price, reference = effective_price(pricing, is_clearance=False)
    assert price == 24.99
    assert reference is None


def test_stock_quantity_from_pickup_location():
    raw = {
        "fulfillment": {
            "fulfillmentOptions": [
                {"type": "pickup", "fulfillable": True, "services": [
                    {"type": "bopis", "locations": [{"inventory": {"isInStock": True, "quantity": 5}, "locationId": "4403"}]},
                ]},
            ]
        }
    }
    assert stock_quantity(raw) == 5


def test_stock_quantity_none_when_not_in_stock_anywhere():
    raw = {
        "fulfillment": {
            "fulfillmentOptions": [
                {"type": "pickup", "fulfillable": False, "services": [
                    {"type": "bopis", "locations": [{"inventory": {"isInStock": False, "quantity": 0}, "locationId": "4403"}]},
                ]},
            ]
        }
    }
    assert stock_quantity(raw) is None


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
