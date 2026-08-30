"""Generic pacing/backoff engine. Adapters declare a RateLimitPolicy
(base.py); this module enforces it identically for every retailer so no
adapter has to reimplement jitter/backoff logic.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from adapters.base import RateLimitPolicy

logger = logging.getLogger("clearance_scout.ratelimit")


@dataclass
class RateLimiter:
    policy: RateLimitPolicy
    on_event: "callable[[str, str | None], None] | None" = None  # (event_type, detail) -> None, e.g. writes rate_limit_event
    _backoff_until: datetime | None = field(default=None, init=False)
    _backoff_seconds: float = field(default=0.0, init=False)

    def wait_before_next_request(self) -> None:
        """Blocks for the adapter-declared jitter delay, or until an active
        backoff window ends, whichever is relevant right now."""
        now = datetime.now(timezone.utc)
        if self._backoff_until and now < self._backoff_until:
            remaining = (self._backoff_until - now).total_seconds()
            logger.info("In backoff, sleeping %.0fs more", remaining)
            time.sleep(remaining)
            self._backoff_until = None
            self._emit("backoff_end", None)
            return

        delay = random.uniform(self.policy.min_delay_seconds, self.policy.max_delay_seconds)
        time.sleep(delay)

    def record_403(self) -> None:
        """Call this the moment a request comes back 403/429. Doubles the
        backoff window each consecutive 403 (capped at the policy's
        max_backoff_seconds), matching HDScanner's documented "15 min to
        several hours" behavior."""
        self._backoff_seconds = min(
            self.policy.max_backoff_seconds,
            max(self.policy.backoff_on_403_seconds, self._backoff_seconds * 2),
        )
        self._backoff_until = datetime.now(timezone.utc) + timedelta(seconds=self._backoff_seconds)
        logger.warning("403 received, backing off %.0fs (until %s)", self._backoff_seconds, self._backoff_until)
        self._emit("403", None)
        self._emit("backoff_start", f"{self._backoff_seconds:.0f}s")

    def record_success(self) -> None:
        """Call this after a clean (non-403) response. Resets the backoff
        multiplier — a single stretch of clean requests after a 403 doesn't
        mean the multiplier should stay elevated forever."""
        self._backoff_seconds = 0.0

    @property
    def backing_off_until(self) -> datetime | None:
        return self._backoff_until

    def _emit(self, event_type: str, detail: str | None) -> None:
        if self.on_event:
            self.on_event(event_type, detail)
