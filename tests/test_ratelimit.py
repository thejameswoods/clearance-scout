from __future__ import annotations

from datetime import datetime, timezone

from adapters.base import RateLimitPolicy
from scanner.ratelimit import RateLimiter


def test_403_triggers_backoff_within_declared_window():
    policy = RateLimitPolicy(
        min_delay_seconds=0, max_delay_seconds=0,
        backoff_on_403_seconds=900, max_backoff_seconds=21600,
    )
    events: list[tuple[str, str | None]] = []
    limiter = RateLimiter(policy=policy, on_event=lambda t, d: events.append((t, d)))

    before = datetime.now(timezone.utc)
    limiter.record_403()

    assert limiter.backing_off_until is not None
    delta = (limiter.backing_off_until - before).total_seconds()
    assert 900 <= delta <= 21600
    assert ("403", None) in events
    assert any(t == "backoff_start" for t, _ in events)


def test_repeated_403s_escalate_but_cap_at_max_backoff():
    policy = RateLimitPolicy(
        min_delay_seconds=0, max_delay_seconds=0,
        backoff_on_403_seconds=900, max_backoff_seconds=3600,
    )
    limiter = RateLimiter(policy=policy)

    limiter.record_403()
    first_backoff = limiter._backoff_seconds
    limiter.record_403()
    second_backoff = limiter._backoff_seconds

    assert second_backoff >= first_backoff
    assert second_backoff <= policy.max_backoff_seconds


def test_success_resets_backoff_multiplier():
    policy = RateLimitPolicy(min_delay_seconds=0, max_delay_seconds=0)
    limiter = RateLimiter(policy=policy)

    limiter.record_403()
    assert limiter._backoff_seconds > 0
    limiter.record_success()
    assert limiter._backoff_seconds == 0.0
