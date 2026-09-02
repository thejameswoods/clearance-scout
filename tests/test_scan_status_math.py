"""scanner/settings.py's progress_fraction/eta_seconds -- the pure math
behind the header's live status payload (design-v2 wireframe 5b: "142 of
310 products", the progress bar, "~6 min left"). Pure functions, no DB, no
patchright -- see the module docstring for why this lives here instead of
in scanner/main.py directly."""

from __future__ import annotations

from scanner.settings import eta_seconds, progress_fraction

FULL_PROGRESS = {
    "department_products_total": 310,
    "department_products_checked": 142,
    "avg_department_size": 80.0,
    "departments_total": 10,
    "department_index": 4,
    "stores_total": 5,
    "store_index": 2,
    "products_checked": 1000,
}


# --- progress_fraction --------------------------------------------------

def test_progress_fraction_matches_department_counts():
    assert progress_fraction({"department_products_total": 310, "department_products_checked": 142}) == 142 / 310


def test_progress_fraction_none_before_department_size_known():
    assert progress_fraction({}) is None
    assert progress_fraction({"department_products_total": 0, "department_products_checked": 0}) is None


def test_progress_fraction_clamped_to_one():
    # Defensive -- shouldn't happen in practice, but a caller passing a
    # checked count momentarily ahead of total (e.g. a race on read)
    # shouldn't render a bar past 100%.
    assert progress_fraction({"department_products_total": 10, "department_products_checked": 12}) == 1.0


# --- eta_seconds ----------------------------------------------------------

def test_eta_seconds_none_with_no_elapsed_time():
    assert eta_seconds(FULL_PROGRESS, elapsed_seconds=0) is None


def test_eta_seconds_none_with_nothing_checked_yet():
    progress = {**FULL_PROGRESS, "products_checked": 0}
    assert eta_seconds(progress, elapsed_seconds=60) is None


def test_eta_seconds_none_when_a_required_field_is_missing():
    # avg_department_size hasn't landed yet (no department fully listed) --
    # honest "not enough data" rather than a guess.
    progress = {**FULL_PROGRESS, "avg_department_size": None}
    assert eta_seconds(progress, elapsed_seconds=60) is None


def test_eta_seconds_uses_real_observed_rate():
    # rate = 1000 products / 100s = 10 products/sec.
    # remaining = (310-142) in this department + (10-4)*80 remaining
    # departments this store + (5-2)*10*80 remaining stores
    #           = 168 + 480 + 2400 = 3048
    # eta = 3048 / 10 = 304.8s
    result = eta_seconds(FULL_PROGRESS, elapsed_seconds=100)
    assert result == 304.8


def test_eta_seconds_shrinks_as_more_gets_checked():
    early = eta_seconds({**FULL_PROGRESS, "department_products_checked": 50}, elapsed_seconds=100)
    late = eta_seconds({**FULL_PROGRESS, "department_products_checked": 250}, elapsed_seconds=100)
    assert late < early
