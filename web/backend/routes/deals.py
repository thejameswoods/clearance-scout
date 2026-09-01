from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from common import db
from web.backend import queries

router = APIRouter(prefix="/api/deals", tags=["deals"])


class DeferBody(BaseModel):
    type: str  # 'discount_pct' | 'price' | 'penny'
    value: float | None = None  # unused for 'penny'


@router.get("")
def get_deals(
    status: list[str] | None = Query(default=None),
    retailer: str | None = None,
    store_id: int | None = None,
    department_id: int | None = None,
    department_prefix: str | None = None,
    clearance_only: bool = False,
    penny_only: bool = False,
    min_discount_pct: float | None = None,
    price_min_cents: int | None = None,
    price_max_cents: int | None = None,
    in_stock_only: bool = False,
    search: str | None = None,
    sort: str = "recent",
):
    with db.get_connection() as conn:
        return queries.list_deals(
            conn, status=status, retailer_slug=retailer, store_id=store_id,
            department_id=department_id, department_prefix=department_prefix,
            clearance_only=clearance_only,
            penny_only=penny_only, min_discount_pct=min_discount_pct,
            price_min_cents=price_min_cents, price_max_cents=price_max_cents,
            in_stock_only=in_stock_only,
            search=search, sort=sort,
        )


@router.get("/tree")
def get_tree(
    retailer: str | None = None,
    store_id: int | None = None,
    department_id: int | None = None,
    department_prefix: str | None = None,
):
    # One combined response for the sidebar + status bar so a scope change
    # doesn't fire three separate requests. No retailer picked yet (first
    # load) -> default to the first one alphabetically, same as "a
    # retailer is picked, 'All N stores' is the default scope" behavior.
    with db.get_connection() as conn:
        retailers = queries.retailer_store_tree(conn)
        selected_retailer = retailer or (retailers[0]["slug"] if retailers else None)
        departments = (
            queries.department_tree_with_counts(conn, selected_retailer, store_id=store_id)
            if selected_retailer else []
        )
        status_counts = queries.status_bar_counts(
            conn, retailer_slug=retailer, store_id=store_id,
            department_id=department_id, department_prefix=department_prefix,
        )
    return {"retailers": retailers, "selected_retailer": selected_retailer,
            "departments": departments, "status_counts": status_counts}


@router.get("/{deal_id}")
def get_deal(deal_id: int):
    with db.get_connection() as conn:
        deal = queries.deal_detail(conn, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


@router.post("/{deal_id}/save")
def save_to_list(deal_id: int):
    with db.get_connection() as conn:
        queries.set_deal_status(conn, deal_id, "saved")
    return {"ok": True}


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


@router.post("/{deal_id}/defer")
def defer(deal_id: int, body: DeferBody):
    if body.type not in ("discount_pct", "price", "penny"):
        raise HTTPException(status_code=400, detail="type must be discount_pct, price, or penny")
    with db.get_connection() as conn:
        db.defer_deal(conn, deal_id, body.type, body.value)
    return {"ok": True}


@router.post("/{deal_id}/undefer")
def undefer(deal_id: int):
    with db.get_connection() as conn:
        db.undefer_deal(conn, deal_id)
    return {"ok": True}
