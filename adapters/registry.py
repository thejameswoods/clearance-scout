"""Retailer registry. The `RETAILERS` env var (comma-separated slugs) picks
which of these the scanner loads at startup — see scanner/main.py.

Adding a retailer: implement RetailerAdapter in a new `adapters/<slug>/`
package, add one entry here. Nothing else changes.
"""

from __future__ import annotations

from .base import RetailerAdapter
from .home_depot.adapter import HomeDepotAdapter

REGISTRY: dict[str, type[RetailerAdapter]] = {
    "home_depot": HomeDepotAdapter,
}


def build_adapter(slug: str) -> RetailerAdapter:
    try:
        adapter_cls = REGISTRY[slug]
    except KeyError:
        known = ", ".join(sorted(REGISTRY)) or "(none registered)"
        raise ValueError(f"Unknown retailer slug '{slug}'. Known: {known}") from None
    return adapter_cls()
