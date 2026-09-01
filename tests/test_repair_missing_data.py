"""The "Repair missing data" tool -- backfills image_url/canonical_url/
aisle/bay for deals missing them, independent of current clearance/penny
status (unlike check_prices()'s hit-only enrichment gate). Covers three
layers: HomeDepotAdapter.enrich_batch() against a fake browser context,
the common/db.py repair functions (COALESCE-only, never clobber a known
value), and orchestrator.repair_missing_enrichment()'s wiring between
them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from adapters.base import Department, ProductRef, RetailerAdapter, StoreInfo
from adapters.home_depot.adapter import HomeDepotAdapter
from common import db
from scanner.orchestrator import repair_missing_enrichment


# --- HomeDepotAdapter.enrich_batch, against a fake browser context ------

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


def test_enrich_batch_fills_image_canonical_aisle_bay():
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    refs = [
        ProductRef(retailer_product_id="1", name="Item One", department=dept),
        ProductRef(retailer_product_id="2", name="Item Two", department=dept),
    ]
    store = StoreInfo(retailer_store_id="4403", zip_code="00000")

    detail_wave_response = [
        _raw_result(200, {"data": {"product": {
            "identifiers": {"canonicalUrl": "/p/one/1", "storeSkuNumber": "sku-1"},
            "media": {"images": [{"url": "https://img/x-64_<SIZE>.jpg"}]},
        }}}),
        _raw_result(200, {"data": {"product": {
            "identifiers": {"canonicalUrl": "/p/two/2", "storeSkuNumber": "sku-2"},
            "media": {"images": []},
        }}}),
    ]
    aislebay_response = _raw_result(200, {"data": {"aislebay": {"storeSkus": [
        {"storeSkuId": "sku-1", "aisleBayInfo": {"aisle": "12", "bay": "3"}},
        {"storeSkuId": "sku-2", "aisleBayInfo": {"aisle": "7", "bay": "1"}},
    ]}}})

    ctx = _FakeBrowserContext([detail_wave_response, aislebay_response])
    adapter = HomeDepotAdapter()

    result = adapter.enrich_batch(ctx, store, refs)

    assert result["1"] == {
        "canonical_url": "https://www.homedepot.com/p/one/1",
        "image_url": "https://img/x-64_400.jpg", "aisle": "12", "bay": "3",
    }
    assert result["2"]["canonical_url"] == "https://www.homedepot.com/p/two/2"
    assert result["2"]["image_url"] is None
    assert result["2"]["aisle"] == "7"
    # 1 product_detail_wave call + 1 aislebay call (both fit in one 20-cap chunk).
    assert ctx._page.evaluate_call_count == 2


def test_enrich_batch_skips_items_with_no_store_sku_and_items_with_an_error():
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    refs = [
        ProductRef(retailer_product_id="no-sku", name="No SKU", department=dept),
        ProductRef(retailer_product_id="errored", name="Errored", department=dept),
    ]
    store = StoreInfo(retailer_store_id="4403", zip_code="00000")

    detail_wave_response = [
        _raw_result(200, {"data": {"product": {"identifiers": {"canonicalUrl": "/p/x/9"}, "media": {}}}}),
        {"status": 0, "body": None, "error": "boom"},
    ]
    ctx = _FakeBrowserContext([detail_wave_response])
    adapter = HomeDepotAdapter()

    result = adapter.enrich_batch(ctx, store, refs)

    assert result["no-sku"]["canonical_url"] == "https://www.homedepot.com/p/x/9"
    assert result["no-sku"]["aisle"] is None  # no storeSkuNumber -- aislebay never called for it
    assert "errored" not in result  # detail.get("error") -> skipped entirely
    # Only 1 evaluate() call: product_detail_wave. No aislebay call at all,
    # since sku_to_ref_id ends up empty (neither item yielded a store_sku_id).
    assert ctx._page.evaluate_call_count == 1


def test_enrich_batch_returns_empty_dict_on_outer_failure():
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    refs = [ProductRef(retailer_product_id="1", name="Item", department=dept)]
    store = StoreInfo(retailer_store_id="4403", zip_code="00000")

    class _ExplodingPage:
        url = "https://www.homedepot.com/"

        def evaluate(self, script, arg):
            raise RuntimeError("network blew up")

    class _ExplodingCtx:
        @property
        def pages(self):
            return [_ExplodingPage()]

    adapter = HomeDepotAdapter()
    assert adapter.enrich_batch(_ExplodingCtx(), store, refs) == {}


def test_enrich_batch_empty_input_short_circuits():
    adapter = HomeDepotAdapter()
    store = StoreInfo(retailer_store_id="4403", zip_code="00000")
    assert adapter.enrich_batch(object(), store, []) == {}


def test_base_adapter_default_enrich_batch_is_a_noop():
    """Retailers that haven't implemented enrichment get an empty dict
    back, not a crash -- the repair tool treats every input as still
    missing, same as any other enrichment failure."""

    class _MinimalAdapter(RetailerAdapter):
        retailer_slug = "minimal"

        def authenticate(self, browser_ctx):
            raise NotImplementedError

        def find_stores(self, browser_ctx, zip_code, radius_miles):
            raise NotImplementedError

        def select_store(self, browser_ctx, store):
            raise NotImplementedError

        def discover_departments(self, browser_ctx):
            raise NotImplementedError

        def list_products(self, browser_ctx, department):
            raise NotImplementedError

        def check_price(self, browser_ctx, product_ref, store):
            raise NotImplementedError

        def detect_clearance(self, raw_response):
            raise NotImplementedError

        def detect_penny(self, observation):
            raise NotImplementedError

        def rate_limit_policy(self):
            raise NotImplementedError

    adapter = _MinimalAdapter()
    store = StoreInfo(retailer_store_id="1", zip_code="00000")
    assert adapter.enrich_batch(None, store, [ProductRef(retailer_product_id="x", name="X", department=None)]) == {}


# --- common/db.py repair functions (DB-layer, COALESCE semantics) -------

def _seed_deal(conn, *, sku="sku-1", retailer_store_id="store-1", image_url=None, canonical_url=None, aisle=None, bay=None):
    retailer_id = db.upsert_retailer(conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    store_id = db.upsert_store(conn, retailer_id, retailer_store_id, "00000", "Fake Store", None)
    department_id = db.upsert_department(conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(
        conn, retailer_id, sku, "Test Widget", department_id, None, image_url,
        canonical_url=canonical_url,
    )
    if aisle is not None or bay is not None:
        db.upsert_store_product_location(conn, product_id, store_id, aisle, bay)
    observation_id = db.insert_price_observation(
        conn, product_id, store_id, None, datetime.now(timezone.utc),
        price_cents=999, list_price_cents=None, is_clearance=True, is_penny=False,
        fulfillment_state="in_stock", stock_quantity=5, raw_signal={},
    )
    db.upsert_deal_from_observation(conn, product_id, store_id, observation_id, is_clearance=True, is_penny=False)
    return product_id, store_id


def test_get_deals_missing_enrichment_finds_null_image_or_canonical(postgres_conn):
    missing_id, _ = _seed_deal(postgres_conn, sku="sku-missing")
    complete_id, complete_store_id = _seed_deal(
        postgres_conn, sku="sku-complete", image_url="https://img", canonical_url="https://url",
        aisle="1", bay="2",
    )

    rows = db.get_deals_missing_enrichment(postgres_conn)

    product_ids = {r["product_id"] for r in rows}
    assert missing_id in product_ids
    assert complete_id not in product_ids


def test_get_deals_missing_enrichment_finds_missing_store_product_location(postgres_conn):
    # image/canonical present, but no store_product_location row at all.
    product_id, _ = _seed_deal(postgres_conn, image_url="https://img", canonical_url="https://url")

    rows = db.get_deals_missing_enrichment(postgres_conn)

    assert {r["product_id"] for r in rows} == {product_id}


def test_get_deals_missing_enrichment_respects_limit(postgres_conn):
    _seed_deal(postgres_conn, sku="sku-1")
    _seed_deal(postgres_conn, sku="sku-2")

    rows = db.get_deals_missing_enrichment(postgres_conn, limit=1)
    assert len(rows) == 1


def test_repair_product_enrichment_only_fills_null_fields(postgres_conn):
    product_id, _ = _seed_deal(postgres_conn, image_url="https://existing-img")

    db.repair_product_enrichment(postgres_conn, product_id, "https://new-canonical", "https://new-img")

    row = postgres_conn.execute("SELECT image_url, canonical_url FROM product WHERE id = %s", (product_id,)).fetchone()
    assert row["image_url"] == "https://existing-img"  # untouched -- was already set
    assert row["canonical_url"] == "https://new-canonical"  # filled -- was null


def test_repair_product_enrichment_noop_when_nothing_found(postgres_conn):
    product_id, _ = _seed_deal(postgres_conn)
    db.repair_product_enrichment(postgres_conn, product_id, None, None)
    row = postgres_conn.execute("SELECT image_url, canonical_url FROM product WHERE id = %s", (product_id,)).fetchone()
    assert row == {"image_url": None, "canonical_url": None}


def test_repair_store_product_location_coalesces_not_overwrites(postgres_conn):
    product_id, store_id = _seed_deal(postgres_conn, aisle="9", bay=None)

    db.repair_store_product_location(postgres_conn, product_id, store_id, aisle="99", bay="4")

    row = postgres_conn.execute(
        "SELECT aisle, bay FROM store_product_location WHERE product_id = %s AND store_id = %s",
        (product_id, store_id),
    ).fetchone()
    assert row["aisle"] == "9"   # untouched -- was already set
    assert row["bay"] == "4"     # filled -- was null


# --- orchestrator.repair_missing_enrichment (wiring) ---------------------

class _RepairFakeAdapter(RetailerAdapter):
    """Only enrich_batch matters for this orchestrator function -- every
    other abstract method is unused and left unimplemented on purpose."""
    retailer_slug = "fake_retailer"

    def __init__(self, enrich_results: dict[str, dict] | None = None, raise_for_store: str | None = None):
        self.enrich_results = enrich_results or {}
        self.raise_for_store = raise_for_store
        self.calls: list[tuple[str, list[str]]] = []

    def enrich_batch(self, browser_ctx, store, product_refs):
        self.calls.append((store.retailer_store_id, [r.retailer_product_id for r in product_refs]))
        if store.retailer_store_id == self.raise_for_store:
            raise RuntimeError("boom")
        return {
            ref.retailer_product_id: self.enrich_results[ref.retailer_product_id]
            for ref in product_refs if ref.retailer_product_id in self.enrich_results
        }

    def authenticate(self, browser_ctx):
        raise NotImplementedError

    def find_stores(self, browser_ctx, zip_code, radius_miles):
        raise NotImplementedError

    def select_store(self, browser_ctx, store):
        raise NotImplementedError

    def discover_departments(self, browser_ctx):
        raise NotImplementedError

    def list_products(self, browser_ctx, department):
        raise NotImplementedError

    def check_price(self, browser_ctx, product_ref, store):
        raise NotImplementedError

    def detect_clearance(self, raw_response):
        raise NotImplementedError

    def detect_penny(self, observation):
        raise NotImplementedError

    def rate_limit_policy(self):
        raise NotImplementedError


def test_repair_missing_enrichment_fills_and_counts(postgres_conn):
    product_id, store_id = _seed_deal(postgres_conn)
    row = postgres_conn.execute("SELECT retailer_product_id FROM product WHERE id = %s", (product_id,)).fetchone()

    adapter = _RepairFakeAdapter(enrich_results={
        row["retailer_product_id"]: {
            "canonical_url": "https://new-url", "image_url": "https://new-img",
            "aisle": "5", "bay": "2",
        },
    })

    result = repair_missing_enrichment(postgres_conn, browser_ctx=None, adapter=adapter)

    assert result == {
        "attempted": 1, "images_filled": 1, "canonical_filled": 1,
        "aisle_bay_filled": 1, "errors": 0,
    }
    product_row = postgres_conn.execute(
        "SELECT image_url, canonical_url FROM product WHERE id = %s", (product_id,)
    ).fetchone()
    assert product_row["image_url"] == "https://new-img"
    location_row = postgres_conn.execute(
        "SELECT aisle, bay FROM store_product_location WHERE product_id = %s AND store_id = %s",
        (product_id, store_id),
    ).fetchone()
    assert location_row == {"aisle": "5", "bay": "2"}


def test_repair_missing_enrichment_counts_a_miss_as_an_error(postgres_conn):
    _seed_deal(postgres_conn)  # enrich_results stays empty -> every target misses

    result = repair_missing_enrichment(postgres_conn, browser_ctx=None, adapter=_RepairFakeAdapter())

    assert result["attempted"] == 1
    assert result["errors"] == 1
    assert result["images_filled"] == 0


def test_repair_missing_enrichment_groups_by_store_one_call_each(postgres_conn):
    _seed_deal(postgres_conn, sku="sku-1", retailer_store_id="store-1")
    _seed_deal(postgres_conn, sku="sku-2", retailer_store_id="store-2")

    adapter = _RepairFakeAdapter()
    repair_missing_enrichment(postgres_conn, browser_ctx=None, adapter=adapter)

    assert len(adapter.calls) == 2  # one enrich_batch call per distinct store, not per product


def test_repair_missing_enrichment_a_failing_store_does_not_abort_the_others(postgres_conn):
    _seed_deal(postgres_conn, sku="sku-bad", retailer_store_id="bad-store")
    good_id, good_store_id = _seed_deal(postgres_conn, sku="sku-good", retailer_store_id="good-store")
    good_row = postgres_conn.execute("SELECT retailer_product_id FROM product WHERE id = %s", (good_id,)).fetchone()

    adapter = _RepairFakeAdapter(
        raise_for_store="bad-store",
        enrich_results={good_row["retailer_product_id"]: {
            "canonical_url": "https://ok", "image_url": None, "aisle": None, "bay": None,
        }},
    )

    result = repair_missing_enrichment(postgres_conn, browser_ctx=None, adapter=adapter)

    assert result["attempted"] == 1  # only the good store's 1 product was attempted
    assert result["errors"] == 1     # the bad store's product counted as an error
    assert result["canonical_filled"] == 1


def test_repair_missing_enrichment_respects_limit(postgres_conn):
    _seed_deal(postgres_conn, sku="sku-1")
    _seed_deal(postgres_conn, sku="sku-2")

    result = repair_missing_enrichment(postgres_conn, browser_ctx=None, adapter=_RepairFakeAdapter(), limit=1)
    assert result["attempted"] == 1
