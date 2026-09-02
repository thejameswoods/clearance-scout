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


@router.get("/scope")
def scan_scope():
    """Feeds the "Scan Now" dialog (wireframe screen 4b): retailer -> store
    list (with distance/last-scanned) plus a rough per-store-department
    time estimate and the department count currently in scope, so the
    dialog can compute a live "Estimated ~N min" as checkboxes change.
    Departments-to-watch now lives in Postgres (watched_department --
    explicit selection, see common/db.py's get_watched_department_names),
    not the scanner's env-shaped /config, so this no longer needs to ask
    the scanner container for it at all."""
    with db.get_connection() as conn:
        retailers = queries.scan_scope(conn)
        avg_seconds = queries.scan_duration_estimate_seconds(conn)
        for retailer in retailers:
            retailer["watched_department_count"] = queries.retailer_watched_department_count(
                conn, retailer["retailer_id"]
            )
    return {
        "retailers": retailers,
        "avg_seconds_per_store_department": avg_seconds,
    }


@router.post("/trigger")
def trigger_scan(department: str | None = None, store_ids: list[int] | None = None):
    # store_ids from the dashboard are DB store.id values (what every other
    # store-scoped endpoint in this API uses) -- translate to the
    # retailer_store_id strings the scanner/orchestrator actually filters
    # on before forwarding.
    retailer_store_ids: list[str] | None = None
    if store_ids:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT retailer_store_id FROM store WHERE id = ANY(%s)", (store_ids,)
            ).fetchall()
        retailer_store_ids = [r["retailer_store_id"] for r in rows]
        if not retailer_store_ids:
            return {"triggered": False, "error": "none of the given store_ids were found"}

    params: list[tuple[str, str]] = []
    if department:
        params.append(("department", department))
    if retailer_store_ids:
        params.extend(("store_ids", sid) for sid in retailer_store_ids)

    try:
        resp = httpx.post(f"{SCANNER_URL}/trigger-scan", params=params, timeout=5)
        return resp.json()
    except httpx.HTTPError as exc:
        return {"triggered": False, "error": str(exc)}
