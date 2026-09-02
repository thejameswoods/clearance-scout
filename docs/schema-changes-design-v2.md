# Schema changes — design-v2 gap close (backend / lists agent)

There is no migration framework (see `db/init/001_schema.sql`'s header and
`docs/HANDOFF_DEALS_PAGE.md`) -- the live deployment does not re-run the init
script. Run the statements below by hand against the live database, in
order. `db/init/001_schema.sql` has also been updated in place so a fresh
install picks these up automatically.

## 1. `deal.deal_kind`, `deal.check_interval`, `deal.last_checked_at` — the "Watching" status

```sql
ALTER TABLE deal ADD COLUMN deal_kind TEXT NOT NULL DEFAULT 'active_clearance'
    CHECK (deal_kind IN ('active_clearance', 'upcoming_clearance', 'penny'));
ALTER TABLE deal ADD COLUMN check_interval INTERVAL NOT NULL DEFAULT '4 hours';
ALTER TABLE deal ADD COLUMN last_checked_at TIMESTAMPTZ;
```

`last_checked_at` is now set to `now()` by `common/db.upsert_deal_from_observation`
on every insert/update, so it's populated for every deal going forward (backfill
old rows with `UPDATE deal SET last_checked_at = updated_at WHERE last_checked_at
IS NULL;` if you want history to look populated immediately rather than waiting
for the next scan).

**`deal_kind` is NOT set to `'upcoming_clearance'` by anything yet.** The
scanner has no code path that creates or flags a deal for a full-price item
(today a `deal` row only ever comes from a confirmed clearance/penny hit —
see `upsert_deal_from_observation`'s early return when neither is true). That
write path is out of this agent's scope per the coordinating brief. The
columns, the `GET /api/deals/tree` "watching" count, and the row-level fields
are all wired up and correct; the "Watching N" count will read 0 until a
scanner change starts writing `deal_kind='upcoming_clearance'`.

`POST /api/deals/{id}/close-eye` shortens `check_interval` (halves it, floor
15 minutes) for one deal — usable today even though nothing sets
`upcoming_clearance` yet, since any deal can have its check cadence tightened.

## 2. `list_item` — per-list-item state (open / purchased / cant_find / no_longer_needed)

```sql
CREATE TABLE list_item (
    id                 SERIAL PRIMARY KEY,
    deal_id            INTEGER NOT NULL UNIQUE REFERENCES deal(id) ON DELETE CASCADE,
    state              TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open', 'purchased', 'cant_find', 'no_longer_needed')),
    quantity           INTEGER,
    purchased_at       TIMESTAMPTZ,
    cant_find_reason   TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_list_item_state ON list_item (state);
```

**Design note (why `deal_id`, not a fresh `product_id`/`store_id` pair):**
`deal` is already `UNIQUE(product_id, store_id)` — a list item maps 1:1 onto
a deal row, and the store comes for free via `deal.store_id`. A separate
`(product_id, store_id)` pair on `list_item` would just be a redundant copy
of what `deal` already guarantees uniqueness on.

**Backward compat:** `deal.status = 'saved'` still means "this deal is on
some list" (unchanged — `POST /api/deals/{id}/save` still sets it, and
History still reads it). `list_item` layers the finer per-item state on top;
the lists read endpoint (`GET /api/lists`) determines actual list
membership from `list_item.state != 'no_longer_needed'`, not from
`deal.status`, so `no_longer_needed` can remove an item from its list
without touching `deal.status` or `product.dismissed_at` (the handoff's
explicit requirement: "removed from the list, does not re-dismiss the
product").

**can't-find reason** is free text, not an enum — `HANDOFF_DEALS_PAGE.md`'s
"Open questions for the user" lists this as unresolved (gone / mispriced /
wrong aisle / other, or something else). Free text unblocks the walking
view's "reason" link now; narrow it to a `CHECK`'d enum once the user
decides.

**"Clear finished"** (`POST /api/lists/store/{store_id}/clear-finished`)
deletes `list_item` rows in `state = 'purchased'` for that store's deals —
a deliberate hard delete, not a state change, since the handoff doesn't ask
for undo on the bulk clear action (only on the individual per-item
dispositions, which `POST /api/lists/items/{deal_id}/reopen` covers).

## 3. `price_check_counter`, `price_check_rate_bucket` — the header odometer (screen 5b)

```sql
CREATE TABLE price_check_counter (
    id            SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton row
    total_checks  BIGINT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO price_check_counter (id, total_checks) VALUES (1, 0);

CREATE TABLE price_check_rate_bucket (
    bucket_start  TIMESTAMPTZ PRIMARY KEY,
    checks        INTEGER NOT NULL DEFAULT 0
);
```

No price-check counter existed before this — the header's "1,482,916
checks" odometer and "+37 in the last minute" line (wireframe 5b) needed
something an every-2-3s poll can read in O(1), which `price_observation`
(an ever-growing, unbounded time-series) cannot offer via `COUNT(*)`.
`price_check_counter` is a single-row running total; `price_check_rate_bucket`
is minute-bucketed counts so "last minute" only ever sums the current +
previous bucket row instead of scanning `price_observation` by time range.
Both are written by `common/db.py`'s `increment_price_check_total`, called
once per successfully-checked product from `scanner/orchestrator.py`'s two
price-check code paths (`run_scan`, `refresh_single_product`) — riding the
same per-item DB round trip as the existing `insert_price_observation`
call, not a new one. `price_check_rate_bucket` prunes itself opportunistically
(rows older than 5 minutes deleted on every write) — it's meant to stay a
handful of rows, never an accumulating history.

**Backfilling the total on an existing deployment:** the counter starts at
0 regardless of how many rows `price_observation` already has. Run once,
by hand, after applying the two `CREATE TABLE`s above, to seed it with
real history instead of starting the odometer over from zero:

```sql
UPDATE price_check_counter SET total_checks = (SELECT COUNT(*) FROM price_observation) WHERE id = 1;
```

(A one-time `COUNT(*)` here is fine — it's the *live, repeated* read this
whole change exists to avoid, not a one-off backfill.)

## 4. `scan_run.status` — add `'cancelled'` (screen 5b's "Cancel scan")

```sql
DO $$
DECLARE
    existing_constraint text;
BEGIN
    -- Looked up by definition rather than assumed-by-name (unlike
    -- db/init/001_schema.sql's fresh-install version, which can safely
    -- assume Postgres's default "<table>_<column>_check" name since
    -- nothing on a brand-new database could have renamed it) — a live
    -- database's constraint-naming history can't be verified from here.
    SELECT con.conname INTO existing_constraint
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'scan_run' AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%status%running%completed%failed%backoff%';
    IF existing_constraint IS NOT NULL THEN
        EXECUTE format('ALTER TABLE scan_run DROP CONSTRAINT %I', existing_constraint);
    END IF;
END $$;

ALTER TABLE scan_run ADD CONSTRAINT scan_run_status_check
    CHECK (status IN ('running', 'completed', 'failed', 'backoff', 'cancelled'));
```

`scan_run.status` previously had no way to represent "a human stopped
this on purpose" — a cancelled scan either sat `'running'` forever (no
cancel path existed before this change at all) or would have had to be
misreported as `'failed'`. `scanner/orchestrator.py`'s cooperative cancel
(`ScanCancelled`, checked at the same checkpoints `on_progress` already
fires at: store start, department start, price-check heartbeat) closes
out whichever `'prices'`-phase `scan_run` was in flight as `'cancelled'`
before returning early — never leaves a row `'running'` after the scanner
itself has gone back to idle, and never kills the underlying process.

**Not applied retroactively.** Any `scan_run` row already stuck at
`status = 'running'` from before this change existed (e.g. a scan
interrupted by a container restart, back when there was no cancel path to
close it out cleanly) is left as-is — this only affects new cancellations
going forward. Clean those up by hand if desired:
`UPDATE scan_run SET status = 'failed', finished_at = now() WHERE status = 'running' AND started_at < now() - interval '1 day';`
