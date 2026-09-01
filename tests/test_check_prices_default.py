"""RetailerAdapter.check_prices()'s default implementation -- a fallback
that wraps check_price() one item at a time, paced by the generic
RateLimiter, for adapters that don't override it with real batching (see
adapters/home_depot/adapter.py for one that does). Must behave exactly
like the old per-item orchestrator loop it replaces.
"""

from __future__ import annotations

from adapters.base import Department, ProductRef, RateLimitPolicy
from scanner.ratelimit import RateLimiter
from tests.fakes import ConfigurableFakeAdapter, FakeBrowserContext


def _refs(*skus):
    dept = Department(retailer_department_id="dept-1", name="Widgets")
    return [ProductRef(retailer_product_id=sku, name=f"Item {sku}", department=dept) for sku in skus]


def _limiter(adapter):
    return RateLimiter(policy=adapter.rate_limit_policy())


def _zero_backoff_limiter():
    # A real 403 sets a real backoff window (15min default) that the very
    # next wait_before_next_request() call would then actually sleep
    # through -- fine/correct in production, but tests that check a 403
    # followed by another item need a zero backoff so they don't hang.
    return RateLimiter(policy=RateLimitPolicy(
        min_delay_seconds=0, max_delay_seconds=0,
        backoff_on_403_seconds=0, max_backoff_seconds=0,
    ))


def test_yields_a_success_result_per_product():
    adapter = ConfigurableFakeAdapter()
    refs = _refs("a", "b", "c")

    results = list(adapter.check_prices(FakeBrowserContext(), refs, adapter.stores[0], _limiter(adapter)))

    assert len(results) == 3
    assert all(r.error is None and r.observation is not None for r in results)
    assert [r.product_ref.retailer_product_id for r in results] == ["a", "b", "c"]


def test_permission_error_becomes_an_error_result_not_a_raise():
    adapter = ConfigurableFakeAdapter(permission_error_skus={"b"})
    refs = _refs("a", "b", "c")

    results = list(adapter.check_prices(FakeBrowserContext(), refs, adapter.stores[0], _zero_backoff_limiter()))

    assert len(results) == 3  # the whole batch keeps going, "b" failing doesn't kill it
    assert results[1].error is not None
    assert results[1].observation is None
    assert results[0].error is None and results[2].error is None


def test_generic_exception_also_becomes_an_error_result():
    adapter = ConfigurableFakeAdapter(failing_skus={"b"})
    refs = _refs("a", "b", "c")

    results = list(adapter.check_prices(FakeBrowserContext(), refs, adapter.stores[0], _limiter(adapter)))

    assert len(results) == 3
    assert results[1].error is not None
    assert "fake failure for b" in results[1].error


def test_rate_limiter_records_403_on_permission_error():
    adapter = ConfigurableFakeAdapter(permission_error_skus={"a"})
    refs = _refs("a")
    limiter = _limiter(adapter)

    list(adapter.check_prices(FakeBrowserContext(), refs, adapter.stores[0], limiter))

    assert limiter.backing_off_until is not None
