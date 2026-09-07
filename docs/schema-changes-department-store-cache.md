# Schema changes — department/store discovery caching + price_observation dedup

There is no migration framework (see `db/init/001_schema.sql`'s header) -- the
live deployment does not re-run the init script. Run the statements below by
hand against the live database, in order. `db/init/001_schema.sql` has also
been updated in place so a fresh install picks these up automatically.

**Back up first** (same as every prior schema change to this deployment):
```
docker compose exec -T db pg_dump -U clearance_scout -d clearance_scout --clean --if-exists > /root/cs-backups/pre-caching-$(date -u +%Y%m%dT%H%M%SZ).sql
```

## 1. Department/store discovery caching (issues #8, #9)

```sql
ALTER TABLE retailer ADD COLUMN departments_last_discovered_at TIMESTAMPTZ;
ALTER TABLE retailer ADD COLUMN stores_last_discovered_at TIMESTAMPTZ;

ALTER TABLE scanner_settings ADD COLUMN department_discovery_cache_hours DOUBLE PRECISION;
ALTER TABLE scanner_settings ADD COLUMN store_discovery_cache_hours DOUBLE PRECISION;
```

Same idea as the existing `department.products_last_listed_at`/
`product_list_cache_hours` cache (phase 2, product listing), applied to the
two other discovery phases that previously re-ran live on every single scan
for data that barely changes:

- **Department discovery** (`adapters/home_depot/departments.py`'s sitemap
  crawl) — confirmed live 2026-09-07 to average **~27.65 seconds per scan**
  across all 62 scans run so far, for an essentially-static category tree.
- **Store discovery** (`find_stores`) — no isolated timing exists, but it's
  the same category of live, Akamai-guarded network round trip repeated
  every scan for a store list that rarely changes.

Both gate on a `retailer`-level last-discovered timestamp
(`scanner/orchestrator.py`'s `run_scan`, same TTL-check shape as the
existing phase-2 gate) with a manual busting escape hatch each: departments
via the new `POST /api/admin/reset-department-discovery-cache`; stores via
the existing "Rescan store list" Settings button (now also stamps the new
timestamp). Shipped default is `168` hours (1 week) for both — see
`.env.example`'s comment for why that's shorter than phase 2's
effectively-permanent default.

**Neither of these touches price-check freshness** — every store still gets
every filtered product's price checked every scan, unconditionally. This
only skips re-discovering the department tree / store list themselves when
they haven't gone stale.

## 2. `price_observation` dedup — new index (issue #10)

```sql
CREATE INDEX idx_price_observation_product_store_time
    ON price_observation (product_id, store_id, observed_at DESC);
```

Backs `common/db.py`'s `get_latest_price_observation` (the dedup check in
`record_price_observation`, called on every price check) — an exact
`(product_id, store_id)` point lookup that the two existing indexes on this
table (`product_id, observed_at` / `store_id, observed_at`) don't serve
efficiently at scale.

Going forward, a price check whose result is identical to the last one
recorded for that product/store no longer writes a new `price_observation`
row (`deal.last_checked_at`/`updated_at` still advance every scan as
before — only storage growth changes, not freshness). Confirmed live
2026-09-07: **360,805 of 530,649 existing rows (68%) are exact duplicates**
of the immediately-prior check for the same product/store — this stops that
from continuing to accumulate.

## 3. One-time backfill: prune existing duplicate rows (issue #10)

Cleans up the ~68% of already-accumulated duplicate rows described above.
Independent of section 2's index/dedup code — either can be applied before
the other with no interaction (the index just makes future lookups fast;
this backfill only touches what's already there).

**Run `EXPLAIN` first and confirm the row-count estimate looks sane before
running for real.** Apply in one transaction, single-statement:

```sql
BEGIN;

WITH referenced_ids AS (
    -- deal.first_observation_id/latest_observation_id are NOT NULL
    -- REFERENCES price_observation(id) with no ON DELETE clause (default
    -- RESTRICT) -- excluded explicitly here rather than relying on
    -- catching an FK violation row by row.
    SELECT first_observation_id AS id FROM deal
    UNION
    SELECT latest_observation_id AS id FROM deal
),
ranked AS (
    SELECT
        id, product_id, store_id, observed_at,
        price_cents, list_price_cents, is_clearance, is_penny,
        fulfillment_state, stock_quantity,
        ROW_NUMBER() OVER (
            PARTITION BY product_id, store_id ORDER BY observed_at DESC
        ) AS rn_desc,
        LAG(price_cents)       OVER w AS prev_price_cents,
        LAG(list_price_cents)  OVER w AS prev_list_price_cents,
        LAG(is_clearance)      OVER w AS prev_is_clearance,
        LAG(is_penny)          OVER w AS prev_is_penny,
        LAG(fulfillment_state) OVER w AS prev_fulfillment_state,
        LAG(stock_quantity)    OVER w AS prev_stock_quantity
    FROM price_observation
    WINDOW w AS (PARTITION BY product_id, store_id ORDER BY observed_at)
),
to_delete AS (
    SELECT id FROM ranked
    WHERE rn_desc > 1                                   -- not the single most-recent row for the pair
      AND id NOT IN (SELECT id FROM referenced_ids)       -- not a deal's first/latest observation
      AND price_cents       IS NOT DISTINCT FROM prev_price_cents
      AND list_price_cents  IS NOT DISTINCT FROM prev_list_price_cents
      AND is_clearance      IS NOT DISTINCT FROM prev_is_clearance
      AND is_penny          IS NOT DISTINCT FROM prev_is_penny
      AND fulfillment_state IS NOT DISTINCT FROM prev_fulfillment_state
      AND stock_quantity    IS NOT DISTINCT FROM prev_stock_quantity  -- identical to immediately-preceding row
)
DELETE FROM price_observation WHERE id IN (SELECT id FROM to_delete);

COMMIT;
```

Notes:
- `IS NOT DISTINCT FROM`, not `=`, throughout — `list_price_cents`,
  `fulfillment_state`, and `stock_quantity` are all nullable, and plain `=`
  against `NULL` never evaluates true, which would wrongly treat two
  consecutive `NULL`s as "different" and skip an otherwise-eligible
  duplicate.
- Chained duplicates (three or more identical rows in a row) are all
  correctly removed in one pass — `LAG(...) OVER w` always compares each
  row only to its immediate chronological predecessor, not to a "canonical"
  first-in-run row, so this doesn't need to run iteratively.
- Deliberately does **not** attempt to also delete a referenced row and
  repoint the FK to its (also-identical) predecessor — out of scope, adds
  real risk (a live write racing this backfill could clobber its own
  repoint) for comparatively little additional space savings, since only
  two rows per deal are ever protected this way regardless of table size.

**Verify nothing further is eligible** (same query, run as a count instead
of a delete — expect `0`):

```sql
WITH referenced_ids AS (
    SELECT first_observation_id AS id FROM deal
    UNION
    SELECT latest_observation_id AS id FROM deal
),
ranked AS (
    SELECT
        id, product_id, store_id, observed_at,
        price_cents, list_price_cents, is_clearance, is_penny,
        fulfillment_state, stock_quantity,
        ROW_NUMBER() OVER (
            PARTITION BY product_id, store_id ORDER BY observed_at DESC
        ) AS rn_desc,
        LAG(price_cents)       OVER w AS prev_price_cents,
        LAG(list_price_cents)  OVER w AS prev_list_price_cents,
        LAG(is_clearance)      OVER w AS prev_is_clearance,
        LAG(is_penny)          OVER w AS prev_is_penny,
        LAG(fulfillment_state) OVER w AS prev_fulfillment_state,
        LAG(stock_quantity)    OVER w AS prev_stock_quantity
    FROM price_observation
    WINDOW w AS (PARTITION BY product_id, store_id ORDER BY observed_at)
)
SELECT count(*) FROM ranked
WHERE rn_desc > 1
  AND id NOT IN (SELECT id FROM referenced_ids)
  AND price_cents       IS NOT DISTINCT FROM prev_price_cents
  AND list_price_cents  IS NOT DISTINCT FROM prev_list_price_cents
  AND is_clearance      IS NOT DISTINCT FROM prev_is_clearance
  AND is_penny          IS NOT DISTINCT FROM prev_is_penny
  AND fulfillment_state IS NOT DISTINCT FROM prev_fulfillment_state
  AND stock_quantity    IS NOT DISTINCT FROM prev_stock_quantity;
```
