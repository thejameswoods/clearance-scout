"""Read queries backing the dashboard. Kept separate from common/db.py
(which is the write-path shared with the scanner) since these are
dashboard-shaped joins that nothing else needs.
"""

from __future__ import annotations

from typing import Any


def list_deals(
    conn,
    status: list[str] | None = None,
    retailer_slug: str | None = None,
    store_id: int | None = None,
    department_id: int | None = None,
    department_prefix: str | None = None,
    clearance_only: bool = False,
    penny_only: bool = False,
    min_discount_pct: float | None = None,
    search: str | None = None,
    sort: str = "recent",
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []

    if status:
        clauses.append("d.status = ANY(%s)")
        params.append(status)
    else:
        clauses.append("d.status IN ('new', 'active')")

    if retailer_slug:
        clauses.append("r.slug = %s")
        params.append(retailer_slug)
    if store_id:
        clauses.append("d.store_id = %s")
        params.append(store_id)
    if department_id:
        clauses.append("p.department_id = %s")
        params.append(department_id)
    elif department_prefix:
        # "All departments under <root>" (the cascading department filter's
        # top-level-only case) -- Home Depot's department names are a real
        # breadcrumb (see build_department_hierarchy), so a child's full
        # name always starts with its parent's, word-boundary safe via
        # starts_with(name, prefix + " ") rather than a raw LIKE (no
        # wildcard-escaping footgun for a department name containing % or _).
        clauses.append("(dept.name = %s OR starts_with(dept.name, %s))")
        params.append(department_prefix)
        params.append(department_prefix + " ")
    if search:
        clauses.append("p.name ILIKE %s")
        params.append(f"%{search}%")

    where_sql = " AND ".join(clauses)

    order_sql = {
        "recent": "d.updated_at DESC",
        "discount": "discount_pct DESC NULLS LAST",
    }.get(sort, "d.updated_at DESC")

    rows = conn.execute(
        f"""
        SELECT
            d.id AS deal_id, d.status, d.created_at, d.updated_at,
            p.id AS product_id, p.retailer_product_id, p.name AS product_name,
            p.image_url, p.canonical_url, p.department_id, dept.name AS department_name,
            s.id AS store_id, s.name AS store_name, s.address AS store_address,
            s.retailer_store_id,
            r.slug AS retailer_slug, r.display_name AS retailer_name,
            po.price_cents, po.list_price_cents, po.is_clearance, po.is_penny,
            po.fulfillment_state, po.stock_quantity, po.observed_at,
            CASE WHEN po.list_price_cents > 0
                 THEN round(100.0 * (po.list_price_cents - po.price_cents) / po.list_price_cents, 1)
                 ELSE NULL END AS discount_pct,
            spl.aisle, spl.bay
        FROM deal d
        JOIN price_observation po ON po.id = d.latest_observation_id
        JOIN product p ON p.id = d.product_id
        JOIN store s ON s.id = d.store_id
        JOIN retailer r ON r.id = p.retailer_id
        LEFT JOIN department dept ON dept.id = p.department_id
        LEFT JOIN store_product_location spl ON spl.product_id = p.id AND spl.store_id = s.id
        WHERE {where_sql}
        {"AND po.is_clearance" if clearance_only else ""}
        {"AND po.is_penny" if penny_only else ""}
        ORDER BY {order_sql}
        LIMIT 500
        """,
        params,
    ).fetchall()

    if min_discount_pct is not None:
        rows = [r for r in rows if (r["discount_pct"] or 0) >= min_discount_pct]
    return rows


def deal_detail(conn, deal_id: int) -> dict[str, Any] | None:
    deal = conn.execute(
        """
        SELECT d.id AS deal_id, d.status, d.product_id, d.store_id,
               p.name AS product_name, p.image_url, p.retailer_product_id
        FROM deal d JOIN product p ON p.id = d.product_id
        WHERE d.id = %s
        """,
        (deal_id,),
    ).fetchone()
    if not deal:
        return None

    history = conn.execute(
        """
        SELECT observed_at, price_cents, list_price_cents, is_clearance, is_penny
        FROM price_observation
        WHERE product_id = %s
        ORDER BY observed_at DESC
        LIMIT 200
        """,
        (deal["product_id"],),
    ).fetchall()

    stores = conn.execute(
        """
        SELECT DISTINCT s.id, s.name, po.price_cents, po.observed_at
        FROM price_observation po
        JOIN store s ON s.id = po.store_id
        WHERE po.product_id = %s
        ORDER BY po.observed_at DESC
        """,
        (deal["product_id"],),
    ).fetchall()

    deal["price_history"] = history
    deal["stores"] = stores
    return deal


def set_deal_status(conn, deal_id: int, status: str) -> None:
    conn.execute(
        "UPDATE deal SET status = %s, updated_at = now() WHERE id = %s",
        (status, deal_id),
    )


def scan_status_panel(conn) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT r.slug AS retailer_slug, sr.phase, sr.status, sr.triggered_by,
               sr.started_at, sr.finished_at, sr.products_checked, sr.errors_count
        FROM scan_run sr
        JOIN retailer r ON r.id = sr.retailer_id
        ORDER BY sr.started_at DESC
        LIMIT 20
        """
    ).fetchall()


def recent_backoff(conn) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT r.slug AS retailer_slug, rle.event_type, rle.occurred_at, rle.detail
        FROM rate_limit_event rle
        JOIN retailer r ON r.id = rle.retailer_id
        ORDER BY rle.occurred_at DESC
        LIMIT 10
        """
    ).fetchall()


def list_retailers(conn) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT id, slug, display_name FROM retailer ORDER BY display_name"
    ).fetchall()


def list_stores(conn) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT s.id, s.name, s.zip_code, r.slug AS retailer_slug
        FROM store s JOIN retailer r ON r.id = s.retailer_id
        ORDER BY r.slug, s.name
        """
    ).fetchall()


def list_departments(conn) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT dept.id, dept.name, r.slug AS retailer_slug
        FROM department dept JOIN retailer r ON r.id = dept.retailer_id
        ORDER BY r.slug, dept.name
        """
    ).fetchall()


def build_department_hierarchy(names: list[str]) -> list[dict[str, Any]]:
    """Home Depot's own department names are already a full breadcrumb
    flattened into one space-joined string by its URL scheme (see
    adapters/home_depot/departments.py) -- "Electrical Batteries AA
    Batteries" *is* Electrical > Electrical Batteries > AA Batteries, just
    with no separate parent_department_id populated (orchestrator.py never
    resolves that -- a "nice to have", per its own comment). Rather than
    showing the dashboard's department filter as one flat, messy list of
    concatenated strings, reconstruct the hierarchy here: a child's full
    name always starts with its parent's full name, so each name's parent
    is the longest *other* name in the set that's a real word-boundary
    prefix of it. Returns a parent-before-children ordering with `depth`
    (for indentation) and `label` (the name with its parent's prefix
    stripped, so each level only shows what's new)."""
    unique_names = sorted(set(names))
    children: dict[str | None, list[str]] = {}
    parent_of: dict[str, str | None] = {}

    for name in unique_names:
        best_parent = None
        for candidate in unique_names:
            if candidate == name:
                continue
            if name.startswith(candidate + " ") and (best_parent is None or len(candidate) > len(best_parent)):
                best_parent = candidate
        parent_of[name] = best_parent
        children.setdefault(best_parent, []).append(name)

    result: list[dict[str, Any]] = []

    def visit(name: str, depth: int) -> None:
        parent = parent_of[name]
        label = name[len(parent) + 1 :] if parent else name
        result.append({"name": name, "depth": depth, "label": label})
        for child in sorted(children.get(name, [])):
            visit(child, depth + 1)

    for root in sorted(children.get(None, [])):
        visit(root, 0)

    return result


def telegram_binding_status(conn) -> dict[str, Any]:
    row = conn.execute(
        "SELECT count(*) AS alerts_sent, max(sent_at) AS last_alert_at FROM alert_sent"
    ).fetchone()
    return row
