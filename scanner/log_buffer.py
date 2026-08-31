"""In-memory ring buffer of recent log records, exposed via the scanner's
internal /logs endpoint so the dashboard's Logs tab can show live scanner
activity without shelling into the container. Docker's own log driver
already keeps the full history if a deeper dive is ever needed -- this is
deliberately just "the last N lines" for a glance from the dashboard.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any


class RingBufferLogHandler(logging.Handler):
    """Keeps only the most recent `capacity` records in memory, each as a
    plain JSON-serializable dict rather than a logging.LogRecord, so /logs
    can hand them straight to FastAPI with no further conversion."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self._buffer: deque[dict[str, Any]] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        self._buffer.append(
            {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
        )

    def records(self) -> list[dict[str, Any]]:
        return list(self._buffer)
