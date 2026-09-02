from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common import db
from web.backend import queries

router = APIRouter(prefix="/api/settings", tags=["settings"])

SCANNER_URL = os.environ.get("SCANNER_INTERNAL_URL", "http://scanner:8090")


class ScanConfigUpdate(BaseModel):
    """PUT /scan-config's body. A field left as None means "don't touch
    it" (the existing saved value, or the env-var default if nothing's
    been saved yet, stays in effect) -- see common/db.py's
    upsert_scanner_settings and scanner/settings.py's merge_settings.
    watched_departments/watch_keywords are the same comma-separated text
    format as the .env vars they override; an empty string (not None)
    explicitly clears one back to "no filter", same as leaving it blank
    in .env.
    """
    zip_code: str | None = None
    radius_miles: float | None = None
    watched_departments: str | None = None
    watch_keywords: str | None = None
    product_list_cache_hours: float | None = None


@router.get("/retailers")
def retailers():
    with db.get_connection() as conn:
        return queries.list_retailers(conn)


class RetailerMinDiscountUpdate(BaseModel):
    min_discount_pct: float | None = None  # None clears the floor


@router.put("/retailers/{retailer_id}/min-discount")
def update_retailer_min_discount(retailer_id: int, update: RetailerMinDiscountUpdate):
    with db.get_connection() as conn:
        db.set_retailer_min_discount_pct(conn, retailer_id, update.min_discount_pct)
    return {"ok": True}


@router.get("/stores")
def stores():
    with db.get_connection() as conn:
        return queries.list_stores(conn)


@router.get("/departments")
def departments():
    with db.get_connection() as conn:
        rows = queries.list_departments(conn)
    hierarchy = queries.build_department_hierarchy([r["name"] for r in rows])
    by_name = {r["name"]: r for r in rows}
    return [
        {**by_name[h["name"]], "depth": h["depth"], "label": h["label"]}
        for h in hierarchy
    ]


@router.get("/telegram")
def telegram_status():
    with db.get_connection() as conn:
        return queries.telegram_binding_status(conn)


@router.get("/scan-config")
def scan_config():
    # Proxies the scanner's own /config -- same pattern as routes/logs.py
    # and routes/scan.py's status proxy. Non-secret runtime config
    # (ZIP/radius/watch filters/scan timing) -- feeds the Settings tab so
    # this doesn't require SSHing in and reading .env by hand.
    try:
        resp = httpx.get(f"{SCANNER_URL}/config", timeout=5)
        return resp.json()
    except httpx.HTTPError as exc:
        return {"error": f"scanner unreachable: {exc}"}


@router.put("/scan-config")
def update_scan_config(update: ScanConfigUpdate):
    # Writes straight to Postgres rather than proxying a write through the
    # scanner container's HTTP API -- both containers already share the
    # same DB (see common/db.py), so there's no need for a second network
    # hop just to reach the same table. The scanner picks up the change on
    # its own next read (scanner/main.py's _current_settings(), called
    # fresh at the start of every scan) -- no redeploy or restart needed.
    fields = update.model_dump(exclude_none=True)
    if "zip_code" in fields and not fields["zip_code"].strip():
        # Unlike watched_departments/watch_keywords, an empty ZIP has no
        # valid meaning (find_stores() needs a real one) -- reject rather
        # than silently stripping the scanner of its store-search anchor.
        raise HTTPException(status_code=400, detail="zip_code can't be blank")

    with db.get_connection() as conn:
        db.upsert_scanner_settings(conn, **fields)
    return {"ok": True, "updated": list(fields.keys())}
