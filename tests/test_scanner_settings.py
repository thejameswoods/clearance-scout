"""common/db.py's editable-settings-override table -- lets the dashboard
change ZIP/radius/watch filters/timing without a redeploy (previously
env-var-only, required editing .env and rebuilding a container). Per
retailer -- see db/init/001_schema.sql's scanner_settings docstring."""

from __future__ import annotations

import pytest

from common import db


@pytest.fixture
def retailer_id(postgres_conn):
    return db.upsert_retailer(postgres_conn, "fake_retailer", "Fake Retailer", "https://example.invalid")


def test_no_settings_saved_yet_returns_none(postgres_conn, retailer_id):
    assert db.get_scanner_settings(postgres_conn, retailer_id) is None


def test_upsert_then_get_round_trips(postgres_conn, retailer_id):
    db.upsert_scanner_settings(postgres_conn, retailer_id, zip_code="84105", radius_miles=10.0)

    settings = db.get_scanner_settings(postgres_conn, retailer_id)

    assert settings["zip_code"] == "84105"
    assert settings["radius_miles"] == 10.0
    assert settings["watch_keywords"] is None  # never set -- stays "use env default"


def test_settings_are_scoped_per_retailer(postgres_conn, retailer_id):
    other_id = db.upsert_retailer(postgres_conn, "other_retailer", "Other Retailer", "https://example.invalid")
    db.upsert_scanner_settings(postgres_conn, retailer_id, zip_code="84105")
    db.upsert_scanner_settings(postgres_conn, other_id, zip_code="27514")

    assert db.get_scanner_settings(postgres_conn, retailer_id)["zip_code"] == "84105"
    assert db.get_scanner_settings(postgres_conn, other_id)["zip_code"] == "27514"


def test_partial_upsert_does_not_clobber_other_fields(postgres_conn, retailer_id):
    db.upsert_scanner_settings(postgres_conn, retailer_id, zip_code="84105", radius_miles=10.0)
    db.upsert_scanner_settings(postgres_conn, retailer_id, radius_miles=25.0)

    settings = db.get_scanner_settings(postgres_conn, retailer_id)

    assert settings["zip_code"] == "84105"  # untouched by the second call
    assert settings["radius_miles"] == 25.0  # updated


def test_unknown_field_rejected(postgres_conn, retailer_id):
    with pytest.raises(ValueError):
        db.upsert_scanner_settings(postgres_conn, retailer_id, not_a_real_field="x")


def test_empty_upsert_is_a_noop(postgres_conn, retailer_id):
    db.upsert_scanner_settings(postgres_conn, retailer_id)  # should not raise
    assert db.get_scanner_settings(postgres_conn, retailer_id) is None
