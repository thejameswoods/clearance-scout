"""Read queries backing the dashboard. Kept separate from common/db.py
(which is the write-path shared with the scanner) since these are
dashboard-shaped joins that nothing else needs.
"""

from __future__ import annotations

from typing import Any

from common import db
# Re-exported so existing callers (routes/settings.py's queries.build_department_hierarchy)
# don't need to change -- the implementation moved to common/ so scanner/orchestrator.py
# can share it too, without depending on this (web-only) module.
from common.departments import build_department_hierarchy  # noqa: F401


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


def scan_scope(conn) -> list[dict[str, Any]]:
    """Retailer -> store list for the "Scan Now" dialog (wireframe screen
    4b), with each store's last-radius-search distance and last-scanned
    time so the dialog can show "Chapel Hill #3612 · 6 mi · scanned 2h
    ago" without a live browser lookup. Only retailers with at least one
    `retailer` row appear -- one that's never been scanned (no adapter
    configured, or configured but never run) isn't listed here at all;
    the frontend treats "retailer has zero stores" as its disabled/
    "not connected" case. Excludes stores disabled in Settings -- the
    scanner never scans one regardless of what Scan Now sends (see
    scanner/orchestrator.py's run_scan), so offering a dead checkbox for
    it here would be misleading; re-enable it in Settings to scan it
    again. Contrast with queries.retailer_store_list, the Settings panel's
    own store list, which includes disabled stores so there's something
    to re-enable."""
    retailers: dict[int, dict[str, Any]] = {
        row["id"]: {"retailer_id": row["id"], "slug": row["slug"], "display_name": row["display_name"], "stores": []}
        for row in conn.execute("SELECT id, slug, display_name FROM retailer ORDER BY display_name").fetchall()
    }
    stores = conn.execute(
        """
        SELECT s.retailer_id, s.id AS store_id, s.name, s.retailer_store_id, s.distance_miles,
               (SELECT max(sr.finished_at) FROM scan_run sr
                WHERE sr.store_id = s.id AND sr.status = 'completed') AS last_scanned_at
        FROM store s
        WHERE s.enabled
        ORDER BY s.distance_miles NULLS LAST, s.name
        """
    ).fetchall()
    for row in stores:
        retailer = retailers.get(row["retailer_id"])
        if not retailer:
            continue
        retailer["stores"].append({
            "store_id": row["store_id"], "name": row["name"], "retailer_store_id": row["retailer_store_id"],
            "distance_miles": row["distance_miles"], "last_scanned_at": row["last_scanned_at"],
        })
    return list(retailers.values())


def retailer_watched_department_count(conn, retailer_id: int) -> int:
    """How many of this retailer's known departments are in scope for a
    scan right now -- the "N departments" figure in the Scan Now dialog's
    time estimate. Delegates to common.db.get_watched_department_names
    (the same explicit-selection-plus-descendants expansion the scanner
    itself uses to decide what to request) rather than reimplementing it."""
    names = db.get_watched_department_names(conn, retailer_id)
    if names is None:
        return conn.execute(
            "SELECT count(*) AS n FROM department WHERE retailer_id = %s", (retailer_id,)
        ).fetchone()["n"]
    return len(names)


def scan_duration_estimate_seconds(conn) -> float:
    """Rough per-store-per-department duration, averaged from this
    scanner's own scan_run history (phase='prices', completed runs) --
    "a rough constant is fine to start" per the handoff doc, but real
    history beats a guessed number once there's any to average. Falls
    back to a flat guess when there's no history yet (fresh install)."""
    row = conn.execute(
        """
        SELECT avg(extract(epoch FROM (finished_at - started_at))) AS avg_seconds
        FROM scan_run
        WHERE phase = 'prices' AND status = 'completed' AND finished_at IS NOT NULL
        """
    ).fetchone()
    return float(row["avg_seconds"]) if row and row["avg_seconds"] else 45.0


def list_retailers(conn) -> list[dict[str, Any]]:
    """Settings tab's left nav: every configured retailer with enough to
    render a summary row (store count, enabled state) without fetching
    each one's full panel."""
    return conn.execute(
        """
        SELECT r.id, r.slug, r.display_name, r.min_discount_pct, r.enabled,
               (SELECT count(*) FROM store s WHERE s.retailer_id = r.id) AS store_count
        FROM retailer r
        ORDER BY r.display_name
        """
    ).fetchall()


def retailer_detail(conn, retailer_id: int) -> dict[str, Any] | None:
    """Settings panel header: identity + admin enabled flag + auth health
    (credential_session.status -- written by the scanner on every scan,
    not surfaced anywhere in the dashboard before this)."""
    return conn.execute(
        """
        SELECT r.id, r.slug, r.display_name, r.enabled, r.adapter_version, r.min_discount_pct,
               cs.status AS credential_status
        FROM retailer r
        LEFT JOIN credential_session cs ON cs.retailer_id = r.id AND cs.session_label = 'default'
        WHERE r.id = %s
        """,
        (retailer_id,),
    ).fetchone()


def retailer_store_list(conn, retailer_id: int) -> list[dict[str, Any]]:
    """Settings panel's "Location & stores" list -- unlike scan_scope
    (Scan Now dialog), includes disabled stores too, since this is where
    an admin re-enables one."""
    return conn.execute(
        """
        SELECT s.id AS store_id, s.name, s.retailer_store_id, s.distance_miles, s.enabled,
               (SELECT max(sr.finished_at) FROM scan_run sr
                WHERE sr.store_id = s.id AND sr.status = 'completed') AS last_scanned_at
        FROM store s
        WHERE s.retailer_id = %s
        ORDER BY s.distance_miles NULLS LAST, s.name
        """,
        (retailer_id,),
    ).fetchall()


def retailer_department_tree(conn, retailer_id: int) -> list[dict[str, Any]]:
    """Settings panel's departments-to-watch checkbox tree -- product
    catalog size per node, rolled up to ancestors the same way
    department_tree_with_counts does for the Deals sidebar, but counting
    `product` rows (catalog size) instead of open `deal` rows (what's
    currently on sale) -- the two are answering different questions."""
    dept_rows = conn.execute("SELECT id, name FROM department WHERE retailer_id = %s", (retailer_id,)).fetchall()
    hierarchy = build_department_hierarchy([r["name"] for r in dept_rows])
    id_by_name = {r["name"]: r["id"] for r in dept_rows}

    own_counts = {
        row["name"]: row["product_count"]
        for row in conn.execute(
            """
            SELECT d.name, count(p.id) AS product_count
            FROM department d
            LEFT JOIN product p ON p.department_id = d.id
            WHERE d.retailer_id = %s
            GROUP BY d.name
            """,
            (retailer_id,),
        ).fetchall()
    }

    # Deepest first, same rollup technique as department_tree_with_counts:
    # a parent's own_counts contribution already reflects everything below
    # it by the time IT gets folded into ITS parent.
    total_counts = dict(own_counts)
    for node in sorted(hierarchy, key=lambda n: -n["depth"]):
        parent = node["parent"]
        if parent:
            total_counts[parent] = total_counts.get(parent, 0) + total_counts.get(node["name"], 0)

    return [
        {
            "id": id_by_name.get(node["name"]),
            "name": node["name"], "label": node["label"], "depth": node["depth"], "parent": node["parent"],
            "count": total_counts.get(node["name"], 0),
        }
        for node in hierarchy
    ]


def watched_department_ids(conn, retailer_id: int) -> set[int]:
    """The raw explicit selection (not descendant-expanded) -- which
    checkboxes render checked in the Settings tree. Contrast with
    common.db.get_watched_department_names, which expands to include
    descendants for scan-time filtering."""
    rows = conn.execute(
        "SELECT department_id FROM watched_department WHERE retailer_id = %s", (retailer_id,)
    ).fetchall()
    return {r["department_id"] for r in rows}



def telegram_binding_status(conn) -> dict[str, Any]:
    row = conn.execute(
        "SELECT count(*) AS alerts_sent, max(sent_at) AS last_alert_at FROM alert_sent"
    ).fetchone()
    return row
