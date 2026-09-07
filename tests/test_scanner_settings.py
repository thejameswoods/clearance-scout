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


def test_exclude_keywords_and_mode_round_trip(postgres_conn, retailer_id):
    db.upsert_scanner_settings(
        postgres_conn, retailer_id, exclude_keywords="refill, kit", keyword_filter_mode="regex",
    )

    settings = db.get_scanner_settings(postgres_conn, retailer_id)

    assert settings["exclude_keywords"] == "refill, kit"
    assert settings["keyword_filter_mode"] == "regex"


def test_department_and_store_discovery_cache_hours_round_trip(postgres_conn, retailer_id):
    db.upsert_scanner_settings(
        postgres_conn, retailer_id,
        department_discovery_cache_hours=168.0, store_discovery_cache_hours=72.0,
    )

    settings = db.get_scanner_settings(postgres_conn, retailer_id)

    assert settings["department_discovery_cache_hours"] == 168.0
    assert settings["store_discovery_cache_hours"] == 72.0


# --- store_keyword_filter (issue #1's store-specific override) --------------

@pytest.fixture
def store_id(postgres_conn, retailer_id):
    return db.upsert_store(postgres_conn, retailer_id, "store-1", "00000", "Store 1", None)


def test_no_store_filter_saved_yet_returns_none(postgres_conn, store_id):
    assert db.get_store_keyword_filter(postgres_conn, store_id) is None


def test_set_then_get_store_filter_round_trips(postgres_conn, store_id):
    db.set_store_keyword_filter(
        postgres_conn, store_id, mode="regex", include_keywords="drill", exclude_keywords="cordless",
    )

    filt = db.get_store_keyword_filter(postgres_conn, store_id)

    assert filt == {"mode": "regex", "include_keywords": "drill", "exclude_keywords": "cordless"}


def test_set_store_filter_twice_upserts_not_duplicates(postgres_conn, store_id):
    db.set_store_keyword_filter(postgres_conn, store_id, mode="simple", include_keywords="drill", exclude_keywords=None)
    db.set_store_keyword_filter(postgres_conn, store_id, mode="simple", include_keywords="saw", exclude_keywords=None)

    filt = db.get_store_keyword_filter(postgres_conn, store_id)
    assert filt["include_keywords"] == "saw"


def test_clear_store_filter_removes_the_row(postgres_conn, store_id):
    db.set_store_keyword_filter(postgres_conn, store_id, mode="simple", include_keywords="drill", exclude_keywords=None)
    db.clear_store_keyword_filter(postgres_conn, store_id)

    assert db.get_store_keyword_filter(postgres_conn, store_id) is None


def test_get_store_keyword_filters_for_retailer_keys_by_retailer_store_id(postgres_conn, retailer_id, store_id):
    db.upsert_store(postgres_conn, retailer_id, "store-2", "00000", "Store 2", None)
    db.set_store_keyword_filter(postgres_conn, store_id, mode="simple", include_keywords="drill", exclude_keywords=None)
    # store-2 has no override -- should not appear in the result at all.

    filters = db.get_store_keyword_filters_for_retailer(postgres_conn, retailer_id)

    assert set(filters) == {"store-1"}
    assert filters["store-1"]["include_keywords"] == "drill"
