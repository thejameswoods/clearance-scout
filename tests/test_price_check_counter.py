"""The header odometer (wireframe 5b): common/db.py's
increment_price_check_total (the write side, called from
scanner/orchestrator.py) and web/backend/queries.py's price_check_odometer
(the read side, polled every 2-3s by GET /api/scan/status). No Postgres
COUNT(*) over price_observation anywhere in this path -- that's the whole
point of the dedicated price_check_counter/price_check_rate_bucket tables
(see db/init/001_schema.sql)."""

from __future__ import annotations

from common import db
from web.backend import queries


def test_increment_returns_running_total(postgres_conn):
    assert db.increment_price_check_total(postgres_conn) == 1
    assert db.increment_price_check_total(postgres_conn) == 2
    assert db.increment_price_check_total(postgres_conn, by=5) == 7


def test_total_persists_across_a_fresh_read_never_goes_backwards(postgres_conn):
    """Simulates a scanner restart: nothing about the total lives only in
    the scanner process's memory (unlike the "last minute" rate, which is
    allowed to reset) -- every increment is committed straight to Postgres
    (autocommit, see common/db.py's get_connection), so a fresh read
    (standing in for a new scanner process reading it at startup) must see
    every increment that happened before, never fewer."""
    for _ in range(12):
        db.increment_price_check_total(postgres_conn)

    # A brand-new read of the persisted value -- not the return value of
    # increment_price_check_total, to prove it's really durable, not just
    # an in-memory running counter this test happens to also be holding.
    odometer = queries.price_check_odometer(postgres_conn)
    assert odometer["total"] == 12

    # More checks after the "restart" only ever add on top -- the total
    # must never regress below what was already persisted.
    db.increment_price_check_total(postgres_conn)
    assert queries.price_check_odometer(postgres_conn)["total"] == 13


def test_odometer_starts_at_zero_with_no_checks_yet(postgres_conn):
    odometer = queries.price_check_odometer(postgres_conn)
    assert odometer == {"total": 0, "last_minute": 0}


def test_last_minute_rate_counts_recent_checks(postgres_conn):
    for _ in range(4):
        db.increment_price_check_total(postgres_conn)

    odometer = queries.price_check_odometer(postgres_conn)
    assert odometer["total"] == 4
    assert odometer["last_minute"] == 4


def test_last_minute_rate_excludes_old_buckets(postgres_conn):
    """A price check from several minutes ago shouldn't inflate "+N in the
    last minute" -- price_check_odometer only ever sums the current +
    previous minute bucket (see its docstring), so a bucket well outside
    that window must be excluded even though the running total still
    includes it."""
    postgres_conn.execute(
        "INSERT INTO price_check_rate_bucket (bucket_start, checks) "
        "VALUES (date_trunc('minute', now()) - interval '10 minutes', 999)"
    )
    postgres_conn.execute(
        "UPDATE price_check_counter SET total_checks = 999 WHERE id = 1"
    )

    db.increment_price_check_total(postgres_conn, by=3)  # a real, current check

    odometer = queries.price_check_odometer(postgres_conn)
    assert odometer["total"] == 1002
    assert odometer["last_minute"] == 3  # the stale 999-check bucket is excluded


def test_increment_prunes_stale_rate_buckets(postgres_conn):
    """increment_price_check_total opportunistically deletes rate-bucket
    rows older than 5 minutes on every write -- this table is meant to
    stay a handful of rows, never an accumulating history."""
    postgres_conn.execute(
        "INSERT INTO price_check_rate_bucket (bucket_start, checks) "
        "VALUES (date_trunc('minute', now()) - interval '30 minutes', 1)"
    )

    db.increment_price_check_total(postgres_conn)

    remaining = postgres_conn.execute(
        "SELECT bucket_start FROM price_check_rate_bucket WHERE bucket_start < now() - interval '5 minutes'"
    ).fetchall()
    assert remaining == []
