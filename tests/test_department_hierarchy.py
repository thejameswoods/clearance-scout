"""Pure-function tests for reconstructing department hierarchy from Home
Depot's flattened department names -- no DB involved (see
web/backend/queries.py:build_department_hierarchy for why this exists)."""

from __future__ import annotations

import time

from web.backend.queries import build_department_hierarchy


def test_single_root_no_children():
    result = build_department_hierarchy(["Electrical"])
    assert result == [{"name": "Electrical", "depth": 0, "label": "Electrical", "parent": None}]


def test_reconstructs_three_level_chain():
    names = [
        "Electrical",
        "Electrical Batteries",
        "Electrical Batteries AA Batteries",
    ]
    result = build_department_hierarchy(names)
    assert result == [
        {"name": "Electrical", "depth": 0, "label": "Electrical", "parent": None},
        {"name": "Electrical Batteries", "depth": 1, "label": "Batteries", "parent": "Electrical"},
        {"name": "Electrical Batteries AA Batteries", "depth": 2, "label": "AA Batteries",
         "parent": "Electrical Batteries"},
    ]


def test_siblings_grouped_under_shared_parent_not_interleaved_with_other_roots():
    names = [
        "Electrical",
        "Electrical Doorbells",
        "Electrical Doorbells Bell Wire",
        "Electrical Batteries",
        "Plumbing",
    ]
    result = build_department_hierarchy(names)
    ordered_names = [r["name"] for r in result]
    # Both Electrical children stay contiguous under Electrical, and
    # Plumbing (an unrelated root) doesn't get interleaved between them.
    assert ordered_names.index("Electrical Batteries") < ordered_names.index("Plumbing")
    assert ordered_names.index("Electrical Doorbells Bell Wire") < ordered_names.index("Plumbing")
    assert ordered_names.index("Electrical") < ordered_names.index("Electrical Doorbells")
    assert ordered_names.index("Electrical Doorbells") < ordered_names.index("Electrical Doorbells Bell Wire")


def test_missing_intermediate_level_still_gets_a_sane_parent():
    # No "Electrical Boxes Conduit Fittings" root observed, only a leaf --
    # falls back to the nearest real prefix match rather than crashing.
    names = ["Electrical", "Electrical Boxes Conduit Fittings Struts"]
    result = build_department_hierarchy(names)
    by_name = {r["name"]: r for r in result}
    assert by_name["Electrical Boxes Conduit Fittings Struts"]["depth"] == 1


def test_unrelated_names_are_all_roots_at_depth_zero():
    names = ["Electrical", "Plumbing", "Lighting"]
    result = build_department_hierarchy(names)
    assert all(r["depth"] == 0 for r in result)
    assert {r["name"] for r in result} == set(names)


def test_duplicate_names_deduplicated():
    result = build_department_hierarchy(["Electrical", "Electrical"])
    assert len(result) == 1


def test_scales_to_real_department_counts_without_quadratic_blowup():
    """Confirmed live 2026-09-01: the original nested-loop implementation
    ("for each name, scan every other name for the longest prefix match")
    took 4.3s of a 4.5s /api/deals/tree request at ~5,200 real department
    names -- every sidebar click re-fetches this. A tree this shape (200
    top-level categories x ~25 sub-departments each, matching the real
    department names' structure) should resolve in well under a second on
    the current sort+stack implementation; a regression back to O(n^2)
    here would blow well past that."""
    names = [f"Category {c}" for c in range(200)]
    for c in range(200):
        names += [f"Category {c} Sub {s}" for s in range(25)]
    assert len(names) == 5200

    start = time.monotonic()
    result = build_department_hierarchy(names)
    elapsed = time.monotonic() - start

    assert len(result) == 5200
    assert elapsed < 1.0, f"took {elapsed:.2f}s -- expected well under 1s at this scale"
