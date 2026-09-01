"""Product-level actions -- currently just dismiss ("Not interested"),
which is deliberately product-level and cross-store (see common/db.py's
dismiss_product), unlike the per-store actions in routes/deals.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from common import db

router = APIRouter(prefix="/api/products", tags=["products"])


@router.post("/{product_id}/dismiss")
def dismiss_product(product_id: int):
    with db.get_connection() as conn:
        db.dismiss_product(conn, product_id)
    return {"ok": True}


@router.post("/{product_id}/undismiss")
def undismiss_product(product_id: int):
    # Backs the Deals page's "undo" affordance on a "Not interested" click.
    with db.get_connection() as conn:
        db.undismiss_product(conn, product_id)
    return {"ok": True}
