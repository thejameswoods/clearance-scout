"""Product-level actions: dismiss ("Not interested", product-level and
cross-store -- see common/db.py's dismiss_product, unlike the per-store
actions in routes/deals.py) and the on-demand "refresh this item
everywhere" tool, which proxies to the scanner's refresh queue the same
way routes/admin.py proxies repair-missing-data.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter

from common import db

router = APIRouter(prefix="/api/products", tags=["products"])

SCANNER_URL = os.environ.get("SCANNER_INTERNAL_URL", "http://scanner:8090")


@router.post("/{product_id}/dismiss")
def dismiss_product(product_id: int):
    with db.get_connection() as conn:
        db.dismiss_product(conn, product_id)
    return {"ok": True}


@router.post("/{product_id}/undismiss")
def undismiss_product(product_id: int):
    # Backs the Deals page's "undo" affordance on a "Not interested" click.
    with db.get_connection() as conn:
        db.undismiss_product(conn, product_id)
    return {"ok": True}


@router.post("/{product_id}/refresh")
def refresh_product(product_id: int):
    try:
        resp = httpx.post(f"{SCANNER_URL}/refresh-product", params={"product_id": product_id}, timeout=5)
        return resp.json()
    except httpx.HTTPError as exc:
        return {"queued": False, "error": str(exc)}


@router.get("/{product_id}/refresh-status")
def refresh_status(product_id: int):
    try:
        resp = httpx.get(f"{SCANNER_URL}/refresh-status", params={"product_id": product_id}, timeout=5)
        return resp.json()
    except httpx.HTTPError:
        return {"state": "unreachable", "result": None}
