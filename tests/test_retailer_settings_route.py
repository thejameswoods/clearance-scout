"""The per-retailer Settings panel routes (web/backend/routes/settings.py) --
replaced the old single global PUT /scan-config when scanner_settings
became per-retailer (see db/init/001_schema.sql). Uses FastAPI's
TestClient against the real test Postgres so this covers the actual
routes, not just the DB functions underneath them (already covered in
tests/test_scanner_settings.py, tests/test_watched_departments.py,
tests/test_store_scoping.py)."""

from __future__ import annotations

import os

import pytest

from common import db

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


@pytest.fixture
def retailer_id(postgres_conn):
    return db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")


def test_get_retailer_detail_404s_for_unknown_retailer(client):
    resp = client.get("/api/settings/retailers/999999")
    assert resp.status_code == 404


def test_get_retailer_detail_includes_stores_and_watched_flag(client, postgres_conn, retailer_id):
    store_id = db.upsert_store(postgres_conn, retailer_id, "3612", "27514", "Chapel Hill #3612", None)
    dept_id = db.upsert_department(postgres_conn, retailer_id, "electrical", "Electrical", None)
    db.set_watched_departments(postgres_conn, retailer_id, [dept_id])

    resp = client.get(f"/api/settings/retailers/{retailer_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "fake_retailer"
    assert body["enabled"] is True
    assert [s["store_id"] for s in body["stores"]] == [store_id]
    watched = {d["id"]: d["watched"] for d in body["departments"]}
    assert watched[dept_id] is True


def test_put_retailer_config_persists_to_the_db(client, postgres_conn, retailer_id):
    resp = client.put(f"/api/settings/retailers/{retailer_id}", json={"zip_code": "84105", "radius_miles": 10.0})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    settings = db.get_scanner_settings(postgres_conn, retailer_id)
    assert settings["zip_code"] == "84105"
    assert settings["radius_miles"] == 10.0


def test_put_retailer_config_rejects_blank_zip(client, retailer_id):
    resp = client.put(f"/api/settings/retailers/{retailer_id}", json={"zip_code": "   "})
    assert resp.status_code == 400


def test_put_retailer_config_omitted_fields_do_not_touch_existing_values(client, postgres_conn, retailer_id):
    client.put(f"/api/settings/retailers/{retailer_id}", json={"zip_code": "84105", "radius_miles": 10.0})
    client.put(f"/api/settings/retailers/{retailer_id}", json={"radius_miles": 5.0})

    settings = db.get_scanner_settings(postgres_conn, retailer_id)
    assert settings["zip_code"] == "84105"  # untouched by the second call
    assert settings["radius_miles"] == 5.0


def test_put_retailer_config_enabled_toggle(client, postgres_conn, retailer_id):
    resp = client.put(f"/api/settings/retailers/{retailer_id}", json={"enabled": False})

    assert resp.status_code == 200
    assert db.get_retailer_by_slug(postgres_conn, "fake_retailer")["enabled"] is False


def test_put_watched_departments_updates_selection(client, postgres_conn, retailer_id):
    dept_id = db.upsert_department(postgres_conn, retailer_id, "electrical", "Electrical", None)

    resp = client.put(f"/api/settings/retailers/{retailer_id}/departments", json={"department_ids": [dept_id]})

    assert resp.status_code == 200
    assert db.get_watched_department_names(postgres_conn, retailer_id) == {"Electrical"}


def test_put_watched_departments_empty_list_clears_selection(client, postgres_conn, retailer_id):
    dept_id = db.upsert_department(postgres_conn, retailer_id, "electrical", "Electrical", None)
    db.set_watched_departments(postgres_conn, retailer_id, [dept_id])

    resp = client.put(f"/api/settings/retailers/{retailer_id}/departments", json={"department_ids": []})

    assert resp.status_code == 200
    assert db.get_watched_department_names(postgres_conn, retailer_id) is None


def test_put_store_enabled_toggle(client, postgres_conn, retailer_id):
    store_id = db.upsert_store(postgres_conn, retailer_id, "3612", "27514", "Chapel Hill #3612", None)

    resp = client.put(f"/api/settings/stores/{store_id}", json={"enabled": False})

    assert resp.status_code == 200
    assert db.get_disabled_store_ids(postgres_conn, retailer_id) == {"3612"}
