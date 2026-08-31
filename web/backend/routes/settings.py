from __future__ import annotations

import os

import httpx
from fastapi import APIRouter

from common import db
from web.backend import queries

router = APIRouter(prefix="/api/settings", tags=["settings"])

SCANNER_URL = os.environ.get("SCANNER_INTERNAL_URL", "http://scanner:8090")


@router.get("/retailers")
def retailers():
    with db.get_connection() as conn:
        return queries.list_retailers(conn)


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
    # and routes/scan.py's status proxy. Read-only, non-secret runtime
    # config (ZIP/radius/watch filters/scan timing), not the .env file
    # itself -- feeds the Settings tab so this doesn't require SSHing in.
    try:
        resp = httpx.get(f"{SCANNER_URL}/config", timeout=5)
        return resp.json()
    except httpx.HTTPError as exc:
        return {"error": f"scanner unreachable: {exc}"}
