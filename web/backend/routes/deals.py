from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from common import db
from web.backend import queries

router = APIRouter(prefix="/api/deals", tags=["deals"])


class DeferBody(BaseModel):
    type: str  # 'discount_pct' | 'price' | 'penny'
    value: float | None = None  # unused for 'penny'


class SaveBody(BaseModel):
    # Optional -- most "Want" clicks (screen 2a) don't set a quantity up
    # front; the list screens (3a/3b) let it be set/changed afterward via
    # PUT /api/lists/items/{deal_id}/quantity. A bare POST with no body
    # still works (FastAPI defaults an all-optional Pydantic body).
    quantity: int | None = None


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
def save_to_list(deal_id: int, body: SaveBody | None = None):
    # Puts the deal on its store's list -- db.add_deal_to_list also dual-
    # writes deal.status='saved', unchanged, since History and other
    # existing readers key off that. body is optional so the pre-existing
    # frontend call (a bare POST, no body) keeps working unchanged
    # alongside the list screens' quantity-aware add.
    with db.get_connection() as conn:
        db.add_deal_to_list(conn, deal_id, quantity=body.quantity if body else None)
    return {"ok": True}


@router.post("/{deal_id}/bought")
def mark_bought(deal_id: int):
    # Ensures a list_item row exists (a deal marked bought via this older,
    # direct endpoint may never have gone through /save first, e.g. a test
    # or a future non-list "just buy it" shortcut) then resolves it --
    # add_deal_to_list is idempotent/safe to call on an existing row.
    with db.get_connection() as conn:
        db.add_deal_to_list(conn, deal_id)
        db.mark_list_item_purchased(conn, deal_id)
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


@router.post("/{deal_id}/close-eye")
def close_eye(deal_id: int):
    """Screen 2a's "Close eye" action on a Watching row -- shortens the
    deal's price-check cadence (see db.shorten_check_interval). Works on
    any deal, not just deal_kind='upcoming_clearance' ones -- nothing
    scanner-side reads deal_kind to decide whether to honor check_interval
    yet, so there's no reason to gate this endpoint on it either."""
    with db.get_connection() as conn:
        db.shorten_check_interval(conn, deal_id)
    return {"ok": True}
