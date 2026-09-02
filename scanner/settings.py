"""Pure logic split out of scanner/main.py so it's testable without
patchright being importable -- main.py imports patchright at module level
for the real browser driver, which isn't available (or needed) just to
test this. Started as scanner_settings DB-override merge logic; also now
the header wireframe 5b status-payload math (progress_fraction,
eta_seconds), same rationale.
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


# --- header status payload math (wireframe 5b) -------------------------------

def progress_fraction(progress: dict[str, Any]) -> float | None:
    """0..1 progress through the *current department* -- matches the
    "142 of 310 products" count the header renders right next to the
    progress bar, so the two never disagree. None before a department's
    size is known yet (nothing to show). `progress` is
    scanner/main.py's live `_progress` dict (see orchestrator.py's
    on_progress)."""
    total = progress.get("department_products_total")
    checked = progress.get("department_products_checked")
    if not total:
        return None
    return max(0.0, min(1.0, checked / total))


def eta_seconds(progress: dict[str, Any], elapsed_seconds: float) -> float | None:
    """Real-observed-rate estimate of time left in the *whole scan*
    (distinct from progress_fraction's current-department scope --
    "~6 min left" in the wireframe reads as the whole scan winding down).
    `elapsed_seconds` (wall-clock time since the scan started) is supplied
    by the caller (scanner/main.py's _eta_seconds, using `now` at *read*
    time, not at the time the underlying checkpoint fired) so this stays
    pure and testable without mocking the clock.

    rate = products_checked so far / elapsed_seconds -- both real,
    already-tracked numbers, not a guessed constant. Applied to a
    remaining-item estimate built from what's actually knowable this
    scan: the current department's exact remainder, plus projected
    remaining departments (this store) and remaining stores, using this
    scan's own running average department size (orchestrator.py's
    `avg_department_size` -- departments are the same list at every
    store, see run_scan's docstring, so this scan's own average is a fair
    projection, not an arbitrary guess).

    None whenever there isn't yet enough real data to be honest about it
    (no rate yet, or a needed field hasn't landed in `progress` yet) --
    the frontend should render nothing rather than a fabricated number.
    """
    products_checked = progress.get("products_checked", 0)
    if elapsed_seconds <= 0 or not products_checked:
        return None
    rate = products_checked / elapsed_seconds  # products/sec, real observed

    required = (
        "department_products_total", "department_products_checked", "avg_department_size",
        "departments_total", "department_index", "stores_total", "store_index",
    )
    if any(progress.get(field) is None for field in required):
        return None

    remaining_in_department = max(progress["department_products_total"] - progress["department_products_checked"], 0)
    remaining_departments_this_store = max(progress["departments_total"] - progress["department_index"], 0)
    remaining_stores = max(progress["stores_total"] - progress["store_index"], 0)
    avg_department_size = progress["avg_department_size"]

    remaining_items = (
        remaining_in_department
        + remaining_departments_this_store * avg_department_size
        + remaining_stores * progress["departments_total"] * avg_department_size
    )
    return remaining_items / rate
