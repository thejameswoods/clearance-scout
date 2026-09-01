"""Data-repair tools -- the Settings tab's "Data tools" section. Exists
because the alternative, confirmed live 2026-09-01, is SSHing in and
running hand-written SQL directly against production: once to clear a
stale product-list cache, and again to work out why 63 deals had silently
flipped to 'dismissed' with no corresponding request in the web
container's access log (a direct DB write that bypassed the app
entirely). Both of those are real, on-demand operations now instead of
one-off psql sessions.
"""

from __future__ import annotations

from fastapi import APIRouter

from common import db

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
