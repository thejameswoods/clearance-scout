"""HomeDepotAdapter's real batched/concurrent check_prices() override --
modeled on HDScanner's own validated wave approach (see adapter.py's
module-level comments). Covers the pure pieces directly (_wave_pace_level,
_parse_product_detail) and the full wave flow against a fake browser
context that stands in for page.evaluate() -- no real browser involved.
"""

from __future__ import annotations

import json

from adapters.base import Department, ProductRef, RateLimitPolicy, StoreInfo
from adapters.home_depot.adapter import HomeDepotAdapter
from scanner.ratelimit import RateLimiter


# --- _wave_pace_level (pure) -------------------------------------------

def test_wave_pace_level_clean():
    base, jitter, level, resets = HomeDepotAdapter._wave_pace_level(0)
    assert level == "ok"
    assert resets is False
    assert base == 1.0


def test_wave_pace_level_light_does_not_reset():
    base, jitter, level, resets = HomeDepotAdapter._wave_pace_level(2)
    assert level == "light"
    assert resets is False


def test_wave_pace_level_moderate_resets():
    base, jitter, level, resets = HomeDepotAdapter._wave_pace_level(3)
    assert level == "moderate"
    assert resets is True


def test_wave_pace_level_heavy_resets():
    base, jitter, level, resets = HomeDepotAdapter._wave_pace_level(5)
    assert level == "heavy"
    assert resets is True
    assert base == 180.0


def test_wave_pause_noop_on_last_wave():
    adapter = HomeDepotAdapter()
    # If this weren't a no-op, a heavy backoff (180-300s) would hang the test.
    result = adapter._wave_pause(consecutive_failures=5, waves_since_breather=0, is_last_wave=True)
    assert result == (5, 0)


# --- _parse_product_detail (pure) ---------------------------------------

def test_parse_product_detail_extracts_and_fixes_size_template():
    detail = {
        "data": {
            "product": {
                "identifiers": {"canonicalUrl": "/p/Some-Item/12345", "storeSkuNumber": "999"},
                "media": {"images": [{"url": "https://images.thdstatic.com/x-64_<SIZE>.jpg"}]},
            }
        }
    }
    canonical_url, image_url, store_sku_id = HomeDepotAdapter._parse_product_detail(detail)
    assert canonical_url == "https://www.homedepot.com/p/Some-Item/12345"
    assert image_url == "https://images.thdstatic.com/x-64_400.jpg"
    assert store_sku_id == "999"


def test_parse_product_detail_handles_missing_fields():
    canonical_url, image_url, store_sku_id = HomeDepotAdapter._parse_product_detail({})
    assert (canonical_url, image_url, store_sku_id) == (None, None, None)


# --- check_prices() full wave flow, against a fake browser context ------

class _FakePage:
    def __init__(self, evaluate_results):
        self.url = "https://www.homedepot.com/"
        self._results = list(evaluate_results)
        self.evaluate_call_count = 0

    def evaluate(self, script, arg):
        self.evaluate_call_count += 1
        return self._results.pop(0)


class _FakeBrowserContext:
    def __init__(self, evaluate_results):
        self._page = _FakePage(evaluate_results)

    @property
    def pages(self):
        return [self._page]


def _raw_result(status, body_dict):
    return {"status": status, "body": json.dumps(body_dict)}


def _limiter():
    return RateLimiter(policy=RateLimitPolicy(min_delay_seconds=0, max_delay_seconds=0))


def test_check_prices_single_wave_batches_into_one_call_per_phase():
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    refs = [
        ProductRef(retailer_product_id="1", name="Not on clearance", department=dept),
        ProductRef(retailer_product_id="2", name="On clearance", department=dept),
    ]
    store = StoreInfo(retailer_store_id="4403", zip_code="00000")

    # Wave 1 (price check): one page.evaluate() call returns one result
    # per chunk (here: one chunk, since 2 items < PRICE_CHECK_MAX_BATCH).
    price_wave_response = _raw_result(200, {
        "data": {
            "products": [
                {"itemId": "1", "pricing": {"value": 10.0, "original": None, "clearance": None},
                 "fulfillment": {"fulfillmentOptions": []}},
                {"itemId": "2", "pricing": {"value": 5.0, "original": None,
                                             "clearance": {"value": 5.0, "dollarOff": 5.0, "percentageOff": 50.0}},
                 "fulfillment": {"fulfillmentOptions": [
                     {"type": "pickup", "fulfillable": False,
                      "services": [{"type": "bopis", "locations": []}]},
                 ]}},
            ]
        }
    })
    # Enrichment (product_detail_wave): one call for item "2" (the only hit).
    product_detail_response = _raw_result(200, {
        "data": {"product": {"identifiers": {"canonicalUrl": "/p/x/2", "storeSkuNumber": "sku-2"}, "media": {"images": []}}}
    })
    # aislebay: one batched call for the hit's storeSkuId.
    aislebay_response = _raw_result(200, {
        "data": {"aislebay": {"storeSkus": [{"storeSkuId": "sku-2", "aisleBayInfo": {"aisle": "12", "bay": "3"}}]}}
    })

    ctx = _FakeBrowserContext([
        [price_wave_response],       # media_price_inventory_wave -> one result per chunk
        [product_detail_response],   # product_detail_wave -> one result per hit
        aislebay_response,           # aislebay -> a single (non-wave) _graphql() call
    ])

    adapter = HomeDepotAdapter()
    results = list(adapter.check_prices(ctx, refs, store, _limiter()))

    assert len(results) == 2
    by_id = {r.product_ref.retailer_product_id: r for r in results}

    assert by_id["1"].error is None
    assert by_id["1"].observation.is_clearance is False

    hit = by_id["2"]
    assert hit.error is None
    assert hit.observation.is_clearance is True
    assert hit.observation.price_cents == 500
    assert hit.observation.canonical_url == "https://www.homedepot.com/p/x/2"
    assert hit.observation.aisle == "12"
    assert hit.observation.bay == "3"

    # The real point of the rewrite: 3 page.evaluate() calls total for 2
    # items (1 price wave + 1 detail wave + 1 aislebay), not one call per
    # item -- proves batching actually happened, not just correct parsing.
    assert ctx._page.evaluate_call_count == 3


def test_check_prices_item_missing_from_response_becomes_an_error_result():
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    refs = [ProductRef(retailer_product_id="missing-sku", name="Ghost", department=dept)]
    store = StoreInfo(retailer_store_id="4403", zip_code="00000")

    ctx = _FakeBrowserContext([[_raw_result(200, {"data": {"products": []}})]])

    adapter = HomeDepotAdapter()
    results = list(adapter.check_prices(ctx, refs, store, _limiter()))

    assert len(results) == 1
    assert results[0].error is not None
    assert results[0].observation is None


def test_check_prices_403_on_a_chunk_reports_error_without_crashing_the_wave():
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    refs = [ProductRef(retailer_product_id="1", name="Item", department=dept)]
    store = StoreInfo(retailer_store_id="4403", zip_code="00000")

    ctx = _FakeBrowserContext([[{"status": 403, "body": None}]])

    adapter = HomeDepotAdapter()
    limiter = _limiter()
    results = list(adapter.check_prices(ctx, refs, store, limiter))

    assert len(results) == 1
    assert results[0].error is not None
    assert limiter.backing_off_until is not None
