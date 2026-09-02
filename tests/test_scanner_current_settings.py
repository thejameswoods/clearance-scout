"""scanner/settings.py's merge_settings() -- merges env-var defaults with
whatever's saved in the scanner_settings DB table (see
tests/test_scanner_settings.py for the DB layer itself). A saved override
should win field-by-field; an unset field should fall back to the env
default, not to None/blank. Kept in its own module (no patchright import)
so this is testable without a real browser driver installed -- see that
module's docstring.
"""

from __future__ import annotations

from scanner.settings import merge_settings, split_list

ENV_DEFAULTS = {
    "zip_code": "00000",
    "radius_miles": 25.0,
    "watched_departments": None,
    "watch_keywords": None,
    "product_list_cache_hours": 24.0,
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


def test_watched_departments_override_is_split_like_the_env_var():
    override_row = {"watched_departments": "Electrical Wire, Plumbing"}

    settings = merge_settings(ENV_DEFAULTS, override_row)

    assert settings["watched_departments"] == ["Electrical Wire", "Plumbing"]


def test_watched_departments_override_can_clear_back_to_scanning_everything():
    override_row = {"watched_departments": ""}

    settings = merge_settings(ENV_DEFAULTS, override_row)

    assert settings["watched_departments"] is None


def test_split_list_handles_blank_and_whitespace():
    assert split_list(None) is None
    assert split_list("") is None
    assert split_list("  ") is None
    assert split_list("A, B ,  C") == ["A", "B", "C"]
