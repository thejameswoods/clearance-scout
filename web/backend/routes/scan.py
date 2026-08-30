"""Proxies scan status/trigger to the scanner container's internal-only API
(never exposed via NPM) — the web backend is the only thing outside the
scanner container that needs to reach it."""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter

from common import db
from web.backend import queries

router = APIRouter(prefix="/api/scan", tags=["scan"])

SCANNER_URL = os.environ.get("SCANNER_INTERNAL_URL", "http://scanner:8090")


@router.get("/status")
def scan_status():
    with db.get_connection() as conn:
        recent_runs = queries.scan_status_panel(conn)
        backoff_events = queries.recent_backoff(conn)
    scanner_live_status = {}
    try:
        resp = httpx.get(f"{SCANNER_URL}/status", timeout=5)
        scanner_live_status = resp.json()
    except httpx.HTTPError:
        scanner_live_status = {"state": "unreachable"}
    return {
        "scanner": scanner_live_status,
        "recent_runs": recent_runs,
        "recent_backoff_events": backoff_events,
    }


@router.post("/trigger")
def trigger_scan(department: str | None = None):
    try:
        resp = httpx.post(
            f"{SCANNER_URL}/trigger-scan",
            params={"department": department} if department else {},
            timeout=5,
        )
        return resp.json()
    except httpx.HTTPError as exc:
        return {"triggered": False, "error": str(exc)}
