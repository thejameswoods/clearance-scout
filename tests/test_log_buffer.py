"""Tests for scanner/log_buffer.py's ring-buffer logging handler."""

from __future__ import annotations

import logging

from scanner.log_buffer import RingBufferLogHandler


def _logger_with_handler(capacity=500):
    handler = RingBufferLogHandler(capacity=capacity)
    logger = logging.getLogger(f"test.log_buffer.{id(handler)}")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    return logger, handler


def test_captures_emitted_records():
    logger, handler = _logger_with_handler()
    logger.info("hello %s", "world")

    records = handler.records()
    assert len(records) == 1
    assert records[0]["level"] == "INFO"
    assert records[0]["logger"] == logger.name
    assert records[0]["message"] == "hello world"
    assert "timestamp" in records[0]


def test_caps_at_capacity_and_drops_oldest():
    logger, handler = _logger_with_handler(capacity=3)
    for i in range(5):
        logger.info("line %d", i)

    records = handler.records()
    assert len(records) == 3
    assert [r["message"] for r in records] == ["line 2", "line 3", "line 4"]


def test_captures_exception_tracebacks():
    logger, handler = _logger_with_handler()
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("scan failed")

    records = handler.records()
    assert "boom" in records[0]["message"]
    assert "Traceback" in records[0]["message"]


def test_records_returns_a_copy_not_a_live_view():
    logger, handler = _logger_with_handler()
    logger.info("first")
    records = handler.records()
    logger.info("second")

    assert len(records) == 1  # snapshot taken before "second" was logged
