"""Proxies the scanner container's recent-log buffer to the dashboard's Logs
tab -- same pattern as routes/scan.py: the scanner's internal API is never
exposed via NPM, only the web backend reaches it directly."""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/logs", tags=["logs"])

SCANNER_URL = os.environ.get("SCANNER_INTERNAL_URL", "http://scanner:8090")


@router.get("")
def recent_logs():
    try:
        resp = httpx.get(f"{SCANNER_URL}/logs", timeout=5)
        return resp.json()
    except httpx.HTTPError as exc:
        return [{"timestamp": None, "level": "ERROR", "logger": "web", "message": f"scanner unreachable: {exc}"}]
