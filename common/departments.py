"""Department-hierarchy reconstruction -- pure, no DB/FastAPI dependency, so
both the web backend (dashboard sidebar/Settings tree) and the scanner
(scan-time descendant expansion for watched departments) can import it
without either depending on the other's stack.
"""

from __future__ import annotations

from typing import Any


def build_department_hierarchy(names: list[str]) -> list[dict[str, Any]]:
    """Home Depot's own department names are already a full breadcrumb
    flattened into one space-joined string by its URL scheme (see
    adapters/home_depot/departments.py) -- "Electrical Batteries AA
    Batteries" *is* Electrical > Electrical Batteries > AA Batteries, just
    with no separate parent_department_id populated (orchestrator.py never
    resolves that -- a "nice to have", per its own comment). Rather than
    showing the dashboard's department filter as one flat, messy list of
    concatenated strings, reconstruct the hierarchy here: a child's full
    name always starts with its parent's full name, so each name's parent
    is the longest *other* name in the set that's a real word-boundary
    prefix of it. Returns a parent-before-children ordering with `depth`
    (for indentation), `label` (the name with its parent's prefix
    stripped, so each level only shows what's new), and `parent` (the
    parent's full name, or None for a root -- used by
    department_tree_with_counts to roll a count up through ancestors, and
    by common/db.py's get_watched_department_names to expand a watched
    node down to its descendants).

    Sort + stack, O(n log n) -- confirmed live 2026-09-01: the original
    "for each name, scan every other name for the longest prefix match"
    was O(n^2), 4.3s of a 4.5s request at ~5,200 real department names
    (every sidebar click re-fetches this). Lexicographic sort already
    guarantees every name's descendants form a contiguous block
    immediately after it (a basic property of prefix-sorted strings), so
    a single pass with a stack of "current ancestor chain" finds each
    name's immediate parent by popping ancestors that aren't a real
    prefix of it -- no re-scanning the whole set per name."""
    unique_names = sorted(set(names))
    result: list[dict[str, Any]] = []
    ancestors: list[str] = []  # root-to-current chain of names still "open"

    for name in unique_names:
        while ancestors and not name.startswith(ancestors[-1] + " "):
            ancestors.pop()
        parent = ancestors[-1] if ancestors else None
        label = name[len(parent) + 1 :] if parent else name
        result.append({"name": name, "depth": len(ancestors), "label": label, "parent": parent})
        ancestors.append(name)

    return result
