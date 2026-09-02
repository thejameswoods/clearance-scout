"""Pure merge logic for scanner_settings DB overrides + env-var defaults.
Split out from scanner/main.py so it's testable without patchright being
importable -- main.py imports patchright at module level for the real
browser driver, which isn't available (or needed) just to test this.
"""

from __future__ import annotations

from typing import Any


def split_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    values = [s.strip() for s in value.split(",") if s.strip()]
    return values or None  # empty/unset means "no filter, scan everything"


def merge_settings(env_defaults: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """`override` is a per-retailer scanner_settings DB row (or None if
    nothing's ever been saved for this retailer from the dashboard) -- a
    field that's present but None within it still means "use the env
    default" for that field specifically, matching common/db.py's
    get_scanner_settings docstring, not "use None as the value".
    Departments-to-watch isn't part of this dict -- see
    common/db.py's get_watched_department_names, resolved separately
    since it's explicit-selection-based now, not an env-shaped default."""
    override = {k: v for k, v in (override or {}).items() if v is not None}

    return {
        "zip_code": override.get("zip_code", env_defaults["zip_code"]),
        "radius_miles": override.get("radius_miles", env_defaults["radius_miles"]),
        "watch_keywords": (
            split_list(override["watch_keywords"]) if "watch_keywords" in override
            else env_defaults["watch_keywords"]
        ),
        "product_list_cache_hours": override.get(
            "product_list_cache_hours", env_defaults["product_list_cache_hours"]
        ),
    }
