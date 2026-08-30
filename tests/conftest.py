"""Tests need a real Postgres to run the schema against — no ORM/mocking
layer to fake it with (see common/db.py's docstring for why). Point
TEST_DATABASE_URL at a throwaway database, e.g.:

    docker run --rm -d -p 5433:5432 -e POSTGRES_PASSWORD=test --name cs-test-db postgres:16
    TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres pytest

Tests are skipped (not failed) if TEST_DATABASE_URL isn't reachable, so a
plain `pytest` still works for anyone just checking the adapter/orchestrator
code compiles.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:test@localhost:5433/postgres"
)
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "init" / "001_schema.sql"


@pytest.fixture()
def postgres_conn():
    try:
        conn = psycopg.connect(TEST_DATABASE_URL, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"No test database reachable at TEST_DATABASE_URL ({exc})")

    conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.execute(SCHEMA_PATH.read_text())
    try:
        yield conn
    finally:
        conn.close()
