from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common import db
from web.backend import queries

router = APIRouter(prefix="/api/settings", tags=["settings"])

SCANNER_URL = os.environ.get("SCANNER_INTERNAL_URL", "http://scanner:8090")


@router.get("/retailers")
def retailers():
    with db.get_connection() as conn:
        return queries.list_retailers(conn)


@router.get("/retailers/{retailer_id}")
def retailer_detail(retailer_id: int):
    # Zip/radius/watch_keywords/product_list_cache_hours live partly in
    # env vars the scanner container has and this one doesn't -- proxy to
    # its /config for the merged (env default + DB override) view, same
    # pattern as the old global /scan-config. Everything else here
    # (enabled, stores, departments) has no env-var counterpart and comes
    # straight from Postgres.
    with db.get_connection() as conn:
        header = queries.retailer_detail(conn, retailer_id)
        if not header:
            raise HTTPException(status_code=404, detail="retailer not found")
        stores = queries.retailer_store_list(conn, retailer_id)
        departments = queries.retailer_department_tree(conn, retailer_id)
        watched_ids = queries.watched_department_ids(conn, retailer_id)

    try:
        resp = httpx.get(f"{SCANNER_URL}/config", params={"retailer": header["slug"]}, timeout=5)
        scan_config = resp.json()
    except httpx.HTTPError as exc:
        scan_config = {"error": f"scanner unreachable: {exc}"}

    return {
        **header,
        "scan_config": scan_config,
        "stores": stores,
        "departments": [{**d, "watched": d["id"] in watched_ids} for d in departments],
    }


class RetailerConfigUpdate(BaseModel):
    """PUT /retailers/{id}'s body. A field left as None means "don't touch
    it" (the existing saved value, or the env-var default if nothing's
    been saved yet, stays in effect) -- see common/db.py's
    upsert_scanner_settings and scanner/settings.py's merge_settings.
    watch_keywords is the same comma-separated text format as the .env
    var it overrides; an empty string (not None) explicitly clears it
    back to "no filter", same as leaving it blank in .env. `enabled` is a
    separate write (retailer.enabled), applied independently of whatever
    else is in this request.
    """
    zip_code: str | None = None
    radius_miles: float | None = None
    watch_keywords: str | None = None
    product_list_cache_hours: float | None = None
    enabled: bool | None = None


@router.put("/retailers/{retailer_id}")
def update_retailer_config(retailer_id: int, update: RetailerConfigUpdate):
    fields = update.model_dump(exclude_none=True)
    enabled = fields.pop("enabled", None)
    if "zip_code" in fields and not fields["zip_code"].strip():
        # Unlike watch_keywords, an empty ZIP has no valid meaning
        # (find_stores() needs a real one) -- reject rather than silently
        # stripping the scanner of its store-search anchor.
        raise HTTPException(status_code=400, detail="zip_code can't be blank")

    with db.get_connection() as conn:
        if fields:
            db.upsert_scanner_settings(conn, retailer_id, **fields)
        if enabled is not None:
            db.set_retailer_enabled(conn, retailer_id, enabled)
    return {"ok": True}


class WatchedDepartmentsUpdate(BaseModel):
    department_ids: list[int]


@router.put("/retailers/{retailer_id}/departments")
def update_watched_departments(retailer_id: int, update: WatchedDepartmentsUpdate):
    # Always the full current selection, not a diff -- see
    # common/db.py's set_watched_departments.
    with db.get_connection() as conn:
        db.set_watched_departments(conn, retailer_id, update.department_ids)
    return {"ok": True}


class StoreEnabledUpdate(BaseModel):
    enabled: bool


@router.put("/stores/{store_id}")
def update_store_enabled(store_id: int, update: StoreEnabledUpdate):
    with db.get_connection() as conn:
        db.set_store_enabled(conn, store_id, update.enabled)
    return {"ok": True}


class RetailerMinDiscountUpdate(BaseModel):
    min_discount_pct: float | None = None  # None clears the floor


@router.put("/retailers/{retailer_id}/min-discount")
def update_retailer_min_discount(retailer_id: int, update: RetailerMinDiscountUpdate):
    with db.get_connection() as conn:
        db.set_retailer_min_discount_pct(conn, retailer_id, update.min_discount_pct)
    return {"ok": True}


@router.get("/telegram")
def telegram_status():
    with db.get_connection() as conn:
        return queries.telegram_binding_status(conn)
