from __future__ import annotations

from fastapi import APIRouter

from common import db
from web.backend import queries

router = APIRouter(prefix="/api/settings", tags=["settings"])


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
        return queries.list_departments(conn)


@router.get("/telegram")
def telegram_status():
    with db.get_connection() as conn:
        return queries.telegram_binding_status(conn)
