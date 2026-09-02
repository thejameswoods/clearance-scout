"""web/backend/routes/scan.py's GET /api/scan/status (extended with the
header odometer, wireframe 5b) and POST /api/scan/cancel. Uses FastAPI's
TestClient against the real test Postgres, same pattern as
tests/test_retailer_settings_route.py -- no scanner container in this test
environment, so the scanner-proxy parts degrade gracefully (checked
separately in tests/test_retailer_settings_route.py's
test_rescan_stores_degrades_gracefully_when_scanner_unreachable)."""

from __future__ import annotations

import os

import pytest

from common import db

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:test@localhost:5433/postgres"
)


@pytest.fixture()
def client(postgres_conn):
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from fastapi.testclient import TestClient

    from web.backend.main import app

    return TestClient(app)


def test_status_includes_price_check_odometer_even_with_no_checks_yet(client):
    resp = client.get("/api/scan/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["price_checks"] == {"total": 0, "last_minute": 0}


def test_status_price_check_odometer_reflects_real_totals(client, postgres_conn):
    for _ in range(7):
        db.increment_price_check_total(postgres_conn)

    resp = client.get("/api/scan/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["price_checks"]["total"] == 7
    assert body["price_checks"]["last_minute"] == 7


def test_status_degrades_gracefully_when_scanner_unreachable(client):
    # No scanner container in this test environment -- matches every other
    # scanner-proxy route's posture (see test_retailer_settings_route.py).
    resp = client.get("/api/scan/status")

    assert resp.status_code == 200
    assert resp.json()["scanner"]["state"] == "unreachable"
    # DB-backed parts of the payload must still work even though the
    # scanner itself is unreachable -- they don't depend on it.
    assert "recent_runs" in resp.json()
    assert "price_checks" in resp.json()


def test_cancel_degrades_gracefully_when_scanner_unreachable(client):
    resp = client.post("/api/scan/cancel")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] is False
    assert "error" in body
