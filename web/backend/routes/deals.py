from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from common import db
from web.backend import queries

router = APIRouter(prefix="/api/deals", tags=["deals"])


@router.get("")
def get_deals(
    status: list[str] | None = Query(default=None),
    retailer: str | None = None,
    store_id: int | None = None,
    department_id: int | None = None,
    clearance_only: bool = False,
    penny_only: bool = False,
    min_discount_pct: float | None = None,
    search: str | None = None,
    sort: str = "recent",
):
    with db.get_connection() as conn:
        return queries.list_deals(
            conn, status=status, retailer_slug=retailer, store_id=store_id,
            department_id=department_id, clearance_only=clearance_only,
            penny_only=penny_only, min_discount_pct=min_discount_pct,
            search=search, sort=sort,
        )


@router.get("/{deal_id}")
def get_deal(deal_id: int):
    with db.get_connection() as conn:
        deal = queries.deal_detail(conn, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


@router.post("/{deal_id}/bought")
def mark_bought(deal_id: int):
    with db.get_connection() as conn:
        queries.set_deal_status(conn, deal_id, "bought")
    return {"ok": True}


@router.post("/{deal_id}/dismiss")
def dismiss(deal_id: int):
    with db.get_connection() as conn:
        queries.set_deal_status(conn, deal_id, "dismissed")
    return {"ok": True}
