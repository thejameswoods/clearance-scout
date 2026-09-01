"""Read queries backing the dashboard. Kept separate from common/db.py
(which is the write-path shared with the scanner) since these are
dashboard-shaped joins that nothing else needs.
"""

from __future__ import annotations

from typing import Any


def _scope_clauses(
    retailer_slug: str | None = None,
    store_id: int | None = None,
    department_id: int | None = None,
    department_prefix: str | None = None,
) -> tuple[list[str], list[Any]]:
    """The retailer/store/department scoping shared by list_deals,
    status_bar_counts, and (indirectly, via its own per-department query)
    department_tree_with_counts -- kept in one place so the sidebar's
    counts and the deal list's filtering can't quietly drift apart.
    Assumes the caller's query aliases deal as d, product as p, retailer
    as r, department as dept (list_deals' existing aliases)."""
    clauses = []
    params: list[Any] = []
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
    return clauses, params


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
    price_min_cents: int | None = None,
    price_max_cents: int | None = None,
    in_stock_only: bool = False,
    search: str | None = None,
    sort: str = "recent",
) -> list[dict[str, Any]]:
    # Dismissal is product-level (see common/db.py's dismiss_product), and
    # dismiss_product dual-writes deal.status='dismissed' on every existing
    # row specifically so History (which asks for status=['dismissed']
    # unchanged by this feature) still shows it -- so the product-level
    # exclusion below only applies when the caller ISN'T explicitly asking
    # for dismissed items; otherwise it would contradict its own dual-write.
    clauses = [] if status and "dismissed" in status else ["p.dismissed_at IS NULL"]
    params: list[Any] = []

    if status:
        clauses.append("d.status = ANY(%s)")
        params.append(status)
    else:
        clauses.append("d.status IN ('new', 'active')")

    scope_clauses, scope_params = _scope_clauses(retailer_slug, store_id, department_id, department_prefix)
    clauses += scope_clauses
    params += scope_params

    if search:
        clauses.append("p.name ILIKE %s")
        params.append(f"%{search}%")
    if price_min_cents is not None:
        clauses.append("po.price_cents >= %s")
        params.append(price_min_cents)
    if price_max_cents is not None:
        clauses.append("po.price_cents <= %s")
        params.append(price_max_cents)
    if in_stock_only:
        clauses.append("po.fulfillment_state = 'in_stock'")

    where_sql = " AND ".join(clauses)

    order_sql = {
        "recent": "d.updated_at DESC",
        "oldest": "d.updated_at ASC",
        "discount": "discount_pct DESC NULLS LAST",
        "price": "po.price_cents ASC",
        "stock": "po.stock_quantity DESC NULLS LAST",
    }.get(sort, "d.updated_at DESC")

    rows = conn.execute(
        f"""
        SELECT
            d.id AS deal_id, d.status, d.defer_rule, d.created_at, d.updated_at,
            p.id AS product_id, p.retailer_product_id, p.name AS product_name,
            p.image_url, p.canonical_url, p.department_id, dept.name AS department_name,
            s.id AS store_id, s.name AS store_name, s.address AS store_address,
            s.retailer_store_id,
            r.slug AS retailer_slug, r.display_name AS retailer_name, r.min_discount_pct,
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
        # An explicit filter on this request is the user's own call --
        # it replaces the retailer default rather than stacking with it
        # (a lower explicit value can deliberately see below the floor).
        rows = [r for r in rows if (r["discount_pct"] or 0) >= min_discount_pct]
    else:
        # No explicit filter -- each retailer's own configured floor
        # (retailer.min_discount_pct) applies by default. Penny items are
        # exempt: they're an extreme deal on their own terms, and some
        # have no discount_pct at all (no list_price on record).
        rows = [
            r for r in rows
            if r["is_penny"] or not r["min_discount_pct"] or (r["discount_pct"] or 0) >= r["min_discount_pct"]
        ]
    return rows


def status_bar_counts(
    conn,
    retailer_slug: str | None = None,
    store_id: int | None = None,
    department_id: int | None = None,
    department_prefix: str | None = None,
) -> dict[str, int]:
    """Deals page's status-tag row: Active clearance / Waiting for a
    deeper cut / All, within the current scope. "All" is new+active+
    deferred (everything untriaged-or-held) -- not stale/bought/dismissed,
    same posture as list_deals' default filter."""
    # count(DISTINCT d.product_id), not count(*) -- a scope spanning more
    # than one store (the common case: "all stores" is the default) would
    # otherwise count one product with deals at 2 stores as 2 items
    # (confirmed live 2026-09-01).
    scope_clauses, scope_params = _scope_clauses(retailer_slug, store_id, department_id, department_prefix)
    scope_sql = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""
    # Same retailer-floor default as list_deals: a retailer with
    # min_discount_pct set should show a "count" that matches what the
    # feed underneath it actually displays, not a bigger number that
    # includes deals the floor is about to hide.
    floor_clause = """
        AND (
            po.is_penny
            OR r.min_discount_pct IS NULL
            OR (po.list_price_cents > 0
                AND (100.0 * (po.list_price_cents - po.price_cents) / po.list_price_cents) >= r.min_discount_pct)
        )
    """
    row = conn.execute(
        f"""
        SELECT
            count(DISTINCT d.product_id) FILTER (WHERE d.status IN ('new', 'active')) AS active,
            count(DISTINCT d.product_id) FILTER (WHERE d.status = 'deferred') AS waiting,
            count(DISTINCT d.product_id) FILTER (WHERE d.status IN ('new', 'active', 'deferred')) AS all_open
        FROM deal d
        JOIN price_observation po ON po.id = d.latest_observation_id
        JOIN product p ON p.id = d.product_id
        JOIN store s ON s.id = d.store_id
        JOIN retailer r ON r.id = p.retailer_id
        LEFT JOIN department dept ON dept.id = p.department_id
        WHERE p.dismissed_at IS NULL {scope_sql} {floor_clause}
        """,
        scope_params,
    ).fetchone()
    return {"active": row["active"], "waiting": row["waiting"], "all": row["all_open"]}


def retailer_store_tree(conn) -> list[dict[str, Any]]:
    """Sidebar section 1: retailer -> store, each with an open (new/active,
    not-dismissed) deal count. Per-store counts are safe as a plain
    count(d.id) -- a product has at most one deal row per store (UNIQUE
    product_id, store_id) -- but the retailer-level total is NOT simply
    the sum of its stores' counts (that double-counts a product on sale
    at more than one store, confirmed live 2026-09-01); it's a second,
    separate count(DISTINCT product_id) query, not a Python sum."""
    rows = conn.execute(
        """
        SELECT r.id AS retailer_id, r.slug AS retailer_slug, r.display_name AS retailer_name,
               s.id AS store_id, s.name AS store_name, s.retailer_store_id,
               count(d.id) FILTER (WHERE d.status IN ('new', 'active') AND p.dismissed_at IS NULL) AS open_count
        FROM retailer r
        JOIN store s ON s.retailer_id = r.id
        LEFT JOIN deal d ON d.store_id = s.id
        LEFT JOIN product p ON p.id = d.product_id
        GROUP BY r.id, r.slug, r.display_name, s.id, s.name, s.retailer_store_id
        ORDER BY r.display_name, s.name
        """
    ).fetchall()

    totals = {
        row["retailer_id"]: row["total"]
        for row in conn.execute(
            """
            SELECT r.id AS retailer_id, count(DISTINCT d.product_id) AS total
            FROM retailer r
            JOIN product p ON p.retailer_id = r.id AND p.dismissed_at IS NULL
            JOIN deal d ON d.product_id = p.id AND d.status IN ('new', 'active')
            GROUP BY r.id
            """
        ).fetchall()
    }

    retailers: dict[int, dict[str, Any]] = {}
    for row in rows:
        retailer = retailers.setdefault(row["retailer_id"], {
            "retailer_id": row["retailer_id"], "slug": row["retailer_slug"],
            "display_name": row["retailer_name"], "total": totals.get(row["retailer_id"], 0), "stores": [],
        })
        retailer["stores"].append({
            "store_id": row["store_id"], "name": row["store_name"],
            "retailer_store_id": row["retailer_store_id"], "open_count": row["open_count"],
        })
    return list(retailers.values())


def department_tree_with_counts(conn, retailer_slug: str, store_id: int | None = None) -> list[dict[str, Any]]:
    """Sidebar section 2: this retailer's department tree, reconstructed
    by build_department_hierarchy (department.parent_department_id is
    never actually populated -- see that function's docstring), with an
    open (new/active) deal count per node rolled up to include every
    descendant, matching "downstream departments are included" in scope."""
    dept_rows = conn.execute(
        "SELECT dept.id, dept.name FROM department dept JOIN retailer r ON r.id = dept.retailer_id WHERE r.slug = %s",
        (retailer_slug,),
    ).fetchall()
    hierarchy = build_department_hierarchy([r["name"] for r in dept_rows])
    id_by_name = {r["name"]: r["id"] for r in dept_rows}

    params: list[Any] = []
    store_clause = ""
    if store_id:
        store_clause = "AND d.store_id = %s"
        params.append(store_id)
    params.append(retailer_slug)

    own_counts = {
        row["name"]: row["open_count"]
        for row in conn.execute(
            f"""
            SELECT dept.name, count(DISTINCT p.id) FILTER (WHERE d.id IS NOT NULL) AS open_count
            FROM department dept
            JOIN retailer r ON r.id = dept.retailer_id
            LEFT JOIN product p ON p.department_id = dept.id AND p.dismissed_at IS NULL
            LEFT JOIN deal d ON d.product_id = p.id AND d.status IN ('new', 'active') {store_clause}
            WHERE r.slug = %s
            GROUP BY dept.name
            """,
            params,
        ).fetchall()
    }

    # Roll each node's own count up into every ancestor -- deepest first,
    # so a parent's own_counts contribution already reflects everything
    # below it by the time IT gets folded into ITS parent.
    total_counts = dict(own_counts)
    for node in sorted(hierarchy, key=lambda n: -n["depth"]):
        parent = node["parent"]
        if parent:
            total_counts[parent] = total_counts.get(parent, 0) + total_counts.get(node["name"], 0)

    return [
        {
            "id": id_by_name.get(node["name"]),
            "name": node["name"], "label": node["label"], "depth": node["depth"],
            "parent": node["parent"],
            "count": total_counts.get(node["name"], 0),
        }
        for node in hierarchy
    ]


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

    # store_id/store_name included so a caller can build a coherent
    # single-store price trajectory (the item-detail modal's History
    # narrative) -- these rows span every store carrying the product, and
    # naively narrating them in observed_at order interleaves unrelated
    # stores' prices into one nonsensical up-and-down story.
    history = conn.execute(
        """
        SELECT po.observed_at, po.price_cents, po.list_price_cents, po.is_clearance, po.is_penny,
               po.store_id, s.name AS store_name
        FROM price_observation po
        JOIN store s ON s.id = po.store_id
        WHERE po.product_id = %s
        ORDER BY po.observed_at DESC
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
        "SELECT id, slug, display_name, min_discount_pct FROM retailer ORDER BY display_name"
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
    (for indentation), `label` (the name with its parent's prefix
    stripped, so each level only shows what's new), and `parent` (the
    parent's full name, or None for a root -- used by
    department_tree_with_counts to roll a count up through ancestors).

    Sort + stack, O(n log n) -- confirmed live 2026-09-01: the original
    "for each name, scan every other name for the longest prefix match"
    was O(n^2), 4.3s of a 4.5s request at ~5,200 real department names
    (every sidebar click re-fetches this). Lexicographic sort already
    guarantees every name's descendants form a contiguous block
    immediately after it (a basic property of prefix-sorted strings), so
    a single pass with a stack of "current ancestor chain" finds each
    name's immediate parent by popping ancestors that aren't a real
    prefix of it -- no re-scanning the whole set per name."""
    unique_names = sorted(set(names))
    result: list[dict[str, Any]] = []
    ancestors: list[str] = []  # root-to-current chain of names still "open"

    for name in unique_names:
        while ancestors and not name.startswith(ancestors[-1] + " "):
            ancestors.pop()
        parent = ancestors[-1] if ancestors else None
        label = name[len(parent) + 1 :] if parent else name
        result.append({"name": name, "depth": len(ancestors), "label": label, "parent": parent})
        ancestors.append(name)

    return result


def telegram_binding_status(conn) -> dict[str, Any]:
    row = conn.execute(
        "SELECT count(*) AS alerts_sent, max(sent_at) AS last_alert_at FROM alert_sent"
    ).fetchone()
    return row
