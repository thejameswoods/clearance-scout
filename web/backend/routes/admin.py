"""Data-repair tools -- the Settings tab's "Data tools" section. Exists
because the alternative, confirmed live 2026-09-01, is SSHing in and
running hand-written SQL directly against production: once to clear a
stale product-list cache, again to work out why 63 deals had silently
flipped to 'dismissed' with no corresponding request in the web
container's access log (a direct DB write that bypassed the app
entirely), and again once that turned up ~40% of deals with no image
because enrichment (image_url/canonical_url/aisle/bay) only ever runs at
hit-confirmation time (see adapter.py) -- a deal whose store fell out of
the configured ZIP/radius, or that was only ever a hit before enrichment
existed, never gets it filled in on its own. All three are real,
on-demand operations now instead of one-off psql/SSH sessions.

repair-missing-data proxies to the scanner container (same pattern as
routes/scan.py) rather than talking to Postgres directly, because unlike
the other two tools it needs the scanner's live authenticated browser
session to actually fetch anything from the retailer.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter

from common import db

router = APIRouter(prefix="/api/admin", tags=["admin"])

SCANNER_URL = os.environ.get("SCANNER_INTERNAL_URL", "http://scanner:8090")


@router.post("/recompute-deal-statuses")
def recompute_deal_statuses(override_manual: bool = False):
    with db.get_connection() as conn:
        count = db.recompute_deal_statuses(conn, override_manual=override_manual)
    return {"ok": True, "updated": count}


@router.post("/reset-department-cache")
def reset_department_cache(retailer: str | None = None):
    with db.get_connection() as conn:
        count = db.reset_department_product_cache(conn, retailer_slug=retailer)
    return {"ok": True, "reset": count}


@router.get("/repair-missing-data/count")
def count_missing_data():
    with db.get_connection() as conn:
        rows = db.get_deals_missing_enrichment(conn)
    return {"missing": len(rows)}


@router.post("/repair-missing-data")
def repair_missing_data(limit: int | None = None):
    try:
        resp = httpx.post(
            f"{SCANNER_URL}/repair-missing-data",
            params={"limit": limit} if limit is not None else {},
            timeout=5,
        )
        return resp.json()
    except httpx.HTTPError as exc:
        return {"triggered": False, "error": str(exc)}


@router.get("/repair-missing-data/status")
def repair_missing_data_status():
    try:
        resp = httpx.get(f"{SCANNER_URL}/repair-status", timeout=5)
        return resp.json()
    except httpx.HTTPError:
        return {"state": "unreachable"}
