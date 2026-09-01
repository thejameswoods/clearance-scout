"""common/db.py's editable-settings-override table -- lets the dashboard
change ZIP/radius/watch filters/timing without a redeploy (previously
env-var-only, required editing .env and rebuilding a container)."""

from __future__ import annotations

import pytest

from common import db


def test_no_settings_saved_yet_returns_none(postgres_conn):
    assert db.get_scanner_settings(postgres_conn) is None


def test_upsert_then_get_round_trips(postgres_conn):
    db.upsert_scanner_settings(postgres_conn, zip_code="84105", radius_miles=10.0)

    settings = db.get_scanner_settings(postgres_conn)

    assert settings["zip_code"] == "84105"
    assert settings["radius_miles"] == 10.0
    assert settings["watched_departments"] is None  # never set -- stays "use env default"


def test_partial_upsert_does_not_clobber_other_fields(postgres_conn):
    db.upsert_scanner_settings(postgres_conn, zip_code="84105", radius_miles=10.0)
    db.upsert_scanner_settings(postgres_conn, radius_miles=25.0)

    settings = db.get_scanner_settings(postgres_conn)

    assert settings["zip_code"] == "84105"  # untouched by the second call
    assert settings["radius_miles"] == 25.0  # updated


def test_unknown_field_rejected(postgres_conn):
    with pytest.raises(ValueError):
        db.upsert_scanner_settings(postgres_conn, not_a_real_field="x")


def test_empty_upsert_is_a_noop(postgres_conn):
    db.upsert_scanner_settings(postgres_conn)  # should not raise
    assert db.get_scanner_settings(postgres_conn) is None
