"""Phase 1: department discovery.

Response-shape placeholders (see api_client.py's module docstring for the
capture procedure) — the `_parse_department` mapping is the one thing you
need to adjust once you've captured a real department_list response.
"""

from __future__ import annotations

from typing import Any, Iterator

from ..base import Department
from .api_client import HomeDepotApiClient


def discover_departments(client: HomeDepotApiClient, store_id: str) -> Iterator[Department]:
    response = client.department_list(store_id)
    for raw in response.get("departments", []):
        yield _parse_department(raw)


def _parse_department(raw: dict[str, Any]) -> Department:
    return Department(
        retailer_department_id=str(raw["id"]),
        name=raw["name"],
        parent_department_id=(
            str(raw["parentId"]) if raw.get("parentId") else None
        ),
    )
