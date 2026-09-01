"""The "refresh this one item everywhere" tool (scanner/orchestrator.py's
refresh_single_product) -- checks every store on record for a product's
retailer, one at a time, via the adapter's existing single-item
check_price() path. Covers the orchestrator function directly against a
fake adapter; the queueing that lets multiple refreshes be requested
without racing the same browser_ctx lives in scanner/main.py and isn't
DB-testable the way this is.
"""

from __future__ import annotations

from datetime import datetime, timezone

from adapters.base import (
    AuthResult, Department, PriceObservation, ProductRef, RateLimitPolicy,
    RetailerAdapter, StoreInfo,
)
from common import db
from scanner.orchestrator import refresh_single_product


class _RefreshFakeAdapter(RetailerAdapter):
    retailer_slug = "fake_retailer"

    def __init__(self, results_by_store: dict[str, PriceObservation | Exception]):
        self.results_by_store = results_by_store
        self.selected_stores: list[str] = []

    def select_store(self, browser_ctx, store):
        self.selected_stores.append(store.retailer_store_id)

    def check_price(self, browser_ctx, product_ref, store):
        result = self.results_by_store[store.retailer_store_id]
        if isinstance(result, Exception):
            raise result
        return result

    def authenticate(self, browser_ctx):
        return AuthResult(valid=True)

    def find_stores(self, browser_ctx, zip_code, radius_miles):
        raise NotImplementedError

    def discover_departments(self, browser_ctx):
        raise NotImplementedError

    def list_products(self, browser_ctx, department):
        raise NotImplementedError

    def detect_clearance(self, raw_response):
        raise NotImplementedError

    def detect_penny(self, observation):
        raise NotImplementedError

    def rate_limit_policy(self):
        return RateLimitPolicy(min_delay_seconds=0, max_delay_seconds=0)


def _observation(product_ref, store, *, price_cents=500, is_clearance=True):
    return PriceObservation(
        product_ref=product_ref, store=store, observed_at=datetime.now(timezone.utc),
        price_cents=price_cents, list_price_cents=1000, is_clearance=is_clearance, is_penny=False,
        fulfillment_state="in_stock", stock_quantity=7, aisle="12", bay="004",
    )


def _seed_product(conn, *, sku="sku-1", store_ids=("store-a", "store-b")):
    retailer_id = db.upsert_retailer(conn, "fake_retailer", "Fake Retailer", "https://example.invalid")
    department_id = db.upsert_department(conn, retailer_id, "dept-1", "Widgets", None)
    product_id = db.upsert_product(conn, retailer_id, sku, "Test Widget", department_id, None, None)
    store_ids_actual = [db.upsert_store(conn, retailer_id, s, "00000", s, None) for s in store_ids]
    return retailer_id, product_id, store_ids_actual


def test_refresh_checks_every_store_and_writes_results(postgres_conn):
    _, product_id, _ = _seed_product(postgres_conn, sku="sku-1", store_ids=("store-a", "store-b"))
    dept = Department(retailer_department_id="", name="")
    ref = ProductRef(retailer_product_id="sku-1", name="Test Widget", department=dept)

    adapter = _RefreshFakeAdapter({
        "store-a": _observation(ref, StoreInfo(retailer_store_id="store-a", zip_code="00000"), price_cents=300),
        "store-b": _observation(ref, StoreInfo(retailer_store_id="store-b", zip_code="00000"), price_cents=800, is_clearance=False),
    })

    result = refresh_single_product(postgres_conn, browser_ctx=None, adapter=adapter, product_id=product_id)

    assert result == {"stores_total": 2, "checked": 2, "hits": 1, "errors": 0}
    assert set(adapter.selected_stores) == {"store-a", "store-b"}

    deals = postgres_conn.execute(
        "SELECT s.retailer_store_id, d.status, po.price_cents FROM deal d "
        "JOIN store s ON s.id = d.store_id JOIN price_observation po ON po.id = d.latest_observation_id "
        "WHERE d.product_id = %s ORDER BY s.retailer_store_id", (product_id,),
    ).fetchall()
    assert deals[0]["retailer_store_id"] == "store-a"
    assert deals[0]["status"] == "new"
    assert deals[0]["price_cents"] == 300


def test_refresh_writes_aisle_bay_and_stock(postgres_conn):
    _, product_id, (store_id,) = _seed_product(postgres_conn, sku="sku-1", store_ids=("store-a",))
    dept = Department(retailer_department_id="", name="")
    ref = ProductRef(retailer_product_id="sku-1", name="Test Widget", department=dept)
    adapter = _RefreshFakeAdapter({
        "store-a": _observation(ref, StoreInfo(retailer_store_id="store-a", zip_code="00000")),
    })

    refresh_single_product(postgres_conn, browser_ctx=None, adapter=adapter, product_id=product_id)

    loc = postgres_conn.execute(
        "SELECT aisle, bay FROM store_product_location WHERE product_id = %s AND store_id = %s",
        (product_id, store_id),
    ).fetchone()
    assert loc == {"aisle": "12", "bay": "004"}


def test_refresh_one_store_erroring_does_not_abort_the_others(postgres_conn):
    _, product_id, _ = _seed_product(postgres_conn, sku="sku-1", store_ids=("bad-store", "good-store"))
    dept = Department(retailer_department_id="", name="")
    ref = ProductRef(retailer_product_id="sku-1", name="Test Widget", department=dept)
    adapter = _RefreshFakeAdapter({
        "bad-store": RuntimeError("boom"),
        "good-store": _observation(ref, StoreInfo(retailer_store_id="good-store", zip_code="00000")),
    })

    result = refresh_single_product(postgres_conn, browser_ctx=None, adapter=adapter, product_id=product_id)

    assert result == {"stores_total": 2, "checked": 1, "hits": 1, "errors": 1}


def test_refresh_unknown_product_raises(postgres_conn):
    adapter = _RefreshFakeAdapter({})
    try:
        refresh_single_product(postgres_conn, browser_ctx=None, adapter=adapter, product_id=999999)
        assert False, "expected ValueError"
    except ValueError:
        pass
