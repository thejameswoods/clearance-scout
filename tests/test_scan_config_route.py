"""PUT /api/settings/scan-config -- writes straight to the scanner_settings
table (see common/db.py), which the scanner reads fresh on its next scan.
Uses FastAPI's TestClient against the real test Postgres so this covers
the actual route, not just the DB functions underneath it (already
covered in tests/test_scanner_settings.py)."""

from __future__ import annotations

import os

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:test@localhost:5433/postgres"
)


@pytest.fixture()
def client(postgres_conn):
    # web/backend/main.py's routes read DATABASE_URL lazily via
    # common.db.get_connection() -- point it at the same throwaway DB
    # postgres_conn already set up (schema applied, clean per-test).
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from fastapi.testclient import TestClient

    from web.backend.main import app

    return TestClient(app)


def test_put_scan_config_persists_to_the_db(client, postgres_conn):
    resp = client.put("/api/settings/scan-config", json={"zip_code": "84105", "radius_miles": 10.0})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    from common import db
    settings = db.get_scanner_settings(postgres_conn)
    assert settings["zip_code"] == "84105"
    assert settings["radius_miles"] == 10.0


def test_put_scan_config_rejects_blank_zip(client):
    resp = client.put("/api/settings/scan-config", json={"zip_code": "   "})

    assert resp.status_code == 400


def test_put_scan_config_omitted_fields_do_not_touch_existing_values(client, postgres_conn):
    client.put("/api/settings/scan-config", json={"zip_code": "84105", "radius_miles": 10.0})
    client.put("/api/settings/scan-config", json={"radius_miles": 5.0})

    from common import db
    settings = db.get_scanner_settings(postgres_conn)
    assert settings["zip_code"] == "84105"  # untouched by the second call
    assert settings["radius_miles"] == 5.0
