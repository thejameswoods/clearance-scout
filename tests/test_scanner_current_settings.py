"""scanner/settings.py's merge_settings() -- merges env-var defaults with
whatever's saved in the scanner_settings DB table (see
tests/test_scanner_settings.py for the DB layer itself). A saved override
should win field-by-field; an unset field should fall back to the env
default, not to None/blank. Kept in its own module (no patchright import)
so this is testable without a real browser driver installed -- see that
module's docstring.
"""

from __future__ import annotations

from scanner.settings import merge_settings, parse_store_keyword_filters, split_list

ENV_DEFAULTS = {
    "zip_code": "00000",
    "radius_miles": 25.0,
    "watch_keywords": None,
    "exclude_keywords": None,
    "keyword_filter_mode": "simple",
    "product_list_cache_hours": 24.0,
    "department_discovery_cache_hours": 24.0,
    "store_discovery_cache_hours": 24.0,
}


def test_no_override_saved_uses_env_defaults():
    settings = merge_settings(ENV_DEFAULTS, None)
    assert settings == ENV_DEFAULTS


def test_saved_override_wins_over_env_default():
    override_row = {"zip_code": "90210", "radius_miles": 5.0}

    settings = merge_settings(ENV_DEFAULTS, override_row)

    assert settings["zip_code"] == "90210"
    assert settings["radius_miles"] == 5.0
    # Fields absent from the override row still fall back to env defaults.
    assert settings["product_list_cache_hours"] == 24.0


def test_none_valued_field_in_override_still_falls_back_to_env_default():
    # A real DB row has every column present, most of them NULL until
    # explicitly saved -- NULL must mean "use the env default", not "the
    # value is None" (which would e.g. break ZIP_CODE entirely).
    override_row = {"zip_code": None, "radius_miles": 5.0}

    settings = merge_settings(ENV_DEFAULTS, override_row)

    assert settings["zip_code"] == "00000"
    assert settings["radius_miles"] == 5.0


def test_watch_keywords_override_is_split_like_the_env_var():
    override_row = {"watch_keywords": "wire, romex"}

    settings = merge_settings(ENV_DEFAULTS, override_row)

    assert settings["watch_keywords"] == ["wire", "romex"]


def test_watch_keywords_override_can_clear_back_to_matching_everything():
    override_row = {"watch_keywords": ""}

    settings = merge_settings(ENV_DEFAULTS, override_row)

    assert settings["watch_keywords"] is None


def test_split_list_handles_blank_and_whitespace():
    assert split_list(None) is None
    assert split_list("") is None
    assert split_list("  ") is None
    assert split_list("A, B ,  C") == ["A", "B", "C"]


def test_exclude_keywords_and_mode_merge_like_watch_keywords():
    override_row = {"exclude_keywords": "refill, kit", "keyword_filter_mode": "regex"}

    settings = merge_settings(ENV_DEFAULTS, override_row)

    assert settings["exclude_keywords"] == ["refill", "kit"]
    assert settings["keyword_filter_mode"] == "regex"


def test_keyword_filter_mode_falls_back_to_env_default():
    settings = merge_settings(ENV_DEFAULTS, {"zip_code": "90210"})
    assert settings["keyword_filter_mode"] == "simple"


def test_parse_store_keyword_filters_splits_each_rows_text_fields():
    rows = {
        "store-a": {"mode": "simple", "include_keywords": "drill, saw", "exclude_keywords": None},
        "store-b": {"mode": "regex", "include_keywords": None, "exclude_keywords": "^refill"},
    }

    parsed = parse_store_keyword_filters(rows)

    assert parsed["store-a"] == {"mode": "simple", "include_keywords": ["drill", "saw"], "exclude_keywords": None}
    assert parsed["store-b"] == {"mode": "regex", "include_keywords": None, "exclude_keywords": ["^refill"]}
