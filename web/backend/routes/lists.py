"""Screens 3a (shopping lists, desktop) and 3b (walking view, mobile).

A "list item" here is always addressed by deal_id -- see db/init/001_schema.sql's
list_item docstring for why a fresh list-item id isn't needed: `deal` is
already UNIQUE(product_id, store_id), so a list item maps 1:1 onto a deal
row. Adding a deal to a list in the first place is still
POST /api/deals/{id}/save (web/backend/routes/deals.py) -- unchanged, per
the handoff's explicit requirement to keep it working.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from common import db
from web.backend import queries

router = APIRouter(prefix="/api/lists", tags=["lists"])


class CantFindBody(BaseModel):
    # Free-text, not an enum -- see list_item's schema docstring: the
    # handoff leaves the reason values (gone / mispriced / wrong aisle /
    # other) as an open question for the user. Optional -- the walking
    # view's primary "Can't find" tap needs to work with no reason at all;
    # the "reason" link is where one gets attached after the fact.
    reason: str | None = None


class QuantityBody(BaseModel):
    quantity: int | None = None


@router.get("")
def get_lists():
    with db.get_connection() as conn:
        stores = queries.store_lists(conn)
    return {
        "stores": stores,
        "total_stores": len(stores),
        "total_items": sum(s["counts"]["total"] for s in stores),
    }


@router.post("/items/{deal_id}/purchased")
def mark_purchased(deal_id: int):
    with db.get_connection() as conn:
        db.mark_list_item_purchased(conn, deal_id)
    return {"ok": True}


@router.post("/items/{deal_id}/cant-find")
def mark_cant_find(deal_id: int, body: CantFindBody | None = None):
    with db.get_connection() as conn:
        db.mark_list_item_cant_find(conn, deal_id, body.reason if body else None)
    return {"ok": True}


@router.post("/items/{deal_id}/no-longer-needed")
def mark_no_longer_needed(deal_id: int):
    with db.get_connection() as conn:
        db.mark_list_item_no_longer_needed(conn, deal_id)
    return {"ok": True}


@router.post("/items/{deal_id}/reopen")
def reopen(deal_id: int):
    """Undo -- reverses any of the three resolutions above. Used by both
    3a's per-item undo and 3b's fixed-bottom-bar "Undo" control."""
    with db.get_connection() as conn:
        db.reopen_list_item(conn, deal_id)
    return {"ok": True}


@router.put("/items/{deal_id}/quantity")
def set_quantity(deal_id: int, body: QuantityBody):
    with db.get_connection() as conn:
        db.set_list_item_quantity(conn, deal_id, body.quantity)
    return {"ok": True}


@router.post("/store/{store_id}/clear-finished")
def clear_finished(store_id: int):
    with db.get_connection() as conn:
        cleared = db.clear_finished_list_items(conn, store_id)
    return {"ok": True, "cleared": cleared}
