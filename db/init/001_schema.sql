-- Clearance Scout — retailer-agnostic schema.
-- Every table keys off retailer_id so a second adapter (e.g. Lowe's) needs
-- zero schema changes, only a new `retailer` row.

CREATE TABLE retailer (
    id                SERIAL PRIMARY KEY,
    slug              TEXT NOT NULL UNIQUE,       -- e.g. 'home_depot'
    display_name      TEXT NOT NULL,
    base_url          TEXT NOT NULL,
    adapter_version   TEXT NOT NULL DEFAULT '0',
    -- NULL = no floor (show everything). Applied as list_deals' default
    -- discount-pct floor for this retailer's deals -- confirmed live
    -- 2026-09-01: without one, real-but-marginal 10%-off "clearance"
    -- buries the deals actually worth a trip. An explicit min_discount_pct
    -- filter on a request overrides this per-request, doesn't stack with it.
    min_discount_pct  DOUBLE PRECISION,
    -- Admin on/off (Settings tab) -- independent of credential_session.status
    -- (auth health). A disabled retailer is skipped by the scanner's main
    -- loop entirely, without an env change/redeploy.
    enabled           BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE store (
    id                 SERIAL PRIMARY KEY,
    retailer_id        INTEGER NOT NULL REFERENCES retailer(id) ON DELETE CASCADE,
    retailer_store_id  TEXT NOT NULL,           -- the retailer's own store number
    zip_code           TEXT NOT NULL,
    name               TEXT,
    address            TEXT,
    -- From the adapter's find_stores() radius search -- not persisted
    -- before this, so it was lost by the time anything outside a live scan
    -- (e.g. the Scan Now dialog) wanted to show "6 mi" next to a store.
    -- Refreshed on every scan that store appears in; stale between scans,
    -- same as name/address.
    distance_miles     DOUBLE PRECISION,
    -- Admin on/off (Settings tab) -- excluded from every scan while false.
    -- Deliberately absent from upsert_store's ON CONFLICT DO UPDATE SET (see
    -- common/db.py) so a routine re-upsert never clobbers this choice; only
    -- a brand-new store row gets the default below.
    enabled            BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (retailer_id, retailer_store_id)
);

CREATE TABLE department (
    id                        SERIAL PRIMARY KEY,
    retailer_id               INTEGER NOT NULL REFERENCES retailer(id) ON DELETE CASCADE,
    retailer_department_id    TEXT NOT NULL,
    name                      TEXT NOT NULL,
    parent_department_id      INTEGER REFERENCES department(id) ON DELETE SET NULL,
    products_last_listed_at   TIMESTAMPTZ,
    UNIQUE (retailer_id, retailer_department_id)
);

-- Also the product-ID cache from HDScanner's "phase 2" — a product is only
-- ever (re)discovered, never re-collected from scratch, once it's in here.
CREATE TABLE product (
    id                    SERIAL PRIMARY KEY,
    retailer_id           INTEGER NOT NULL REFERENCES retailer(id) ON DELETE CASCADE,
    retailer_product_id   TEXT NOT NULL,        -- SKU / item ID
    upc                   TEXT,
    name                  TEXT NOT NULL,
    department_id         INTEGER REFERENCES department(id) ON DELETE SET NULL,
    image_url             TEXT,
    canonical_url         TEXT,
    first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Set (once, permanently) when the user disposition's a product as
    -- "not interested" -- product-level and cross-store by design (unlike
    -- deal.status, which is per product+store): the same SKU showing up
    -- at a different store shouldn't need dismissing again.
    dismissed_at          TIMESTAMPTZ,
    UNIQUE (retailer_id, retailer_product_id)
);

CREATE TABLE store_product_location (
    product_id        INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
    store_id          INTEGER NOT NULL REFERENCES store(id) ON DELETE CASCADE,
    aisle             TEXT,
    bay               TEXT,
    last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, store_id)
);

CREATE TABLE scan_run (
    id               SERIAL PRIMARY KEY,
    retailer_id      INTEGER NOT NULL REFERENCES retailer(id) ON DELETE CASCADE,
    store_id         INTEGER REFERENCES store(id) ON DELETE SET NULL,
    phase            TEXT NOT NULL CHECK (phase IN ('departments', 'products', 'prices')),
    status           TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'backoff')),
    triggered_by     TEXT NOT NULL CHECK (triggered_by IN ('scheduled', 'manual', 'telegram')),
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    products_checked INTEGER NOT NULL DEFAULT 0,
    errors_count     INTEGER NOT NULL DEFAULT 0
);

-- Core time-series table: every price check ever made, for cross-store /
-- cross-time comparison (what HDScanner does locally in IndexedDB).
CREATE TABLE price_observation (
    id                SERIAL PRIMARY KEY,
    product_id        INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
    store_id          INTEGER NOT NULL REFERENCES store(id) ON DELETE CASCADE,
    scan_run_id       INTEGER REFERENCES scan_run(id) ON DELETE SET NULL,
    observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    price_cents       INTEGER NOT NULL,
    list_price_cents  INTEGER,
    is_clearance      BOOLEAN NOT NULL DEFAULT false,
    is_penny          BOOLEAN NOT NULL DEFAULT false,
    fulfillment_state TEXT,
    stock_quantity    INTEGER,
    raw_signal        JSONB
);
CREATE INDEX idx_price_observation_product_time ON price_observation (product_id, observed_at DESC);
CREATE INDEX idx_price_observation_store_time ON price_observation (store_id, observed_at DESC);

-- The dashboard/bot's read model — derived from price_observation so nobody
-- re-derives "is this still live" from raw rows on every page load.
CREATE TABLE deal (
    id                     SERIAL PRIMARY KEY,
    product_id             INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
    store_id               INTEGER NOT NULL REFERENCES store(id) ON DELETE CASCADE,
    first_observation_id   INTEGER NOT NULL REFERENCES price_observation(id),
    latest_observation_id  INTEGER NOT NULL REFERENCES price_observation(id),
    status                 TEXT NOT NULL DEFAULT 'new'
                            CHECK (status IN ('new', 'active', 'stale', 'saved', 'bought', 'dismissed', 'deferred')),
    -- Only meaningful when status = 'deferred' -- {"type": "discount_pct" |
    -- "price" | "penny", "value": ...}. Checked against every store's
    -- latest observation of this product once per scan (see
    -- common/db.py's reactivate_satisfied_defers) -- satisfying it
    -- anywhere flips this row back to 'new'.
    defer_rule             JSONB,
    -- deal_kind / check_interval / last_checked_at back the "Watching"
    -- status tag (design-v2 screen 2a): deal_kind='upcoming_clearance' is
    -- a full-price item flagged for closer watching, distinct from
    -- 'active_clearance' (already discounted) and 'penny'. The scanner
    -- does not create or flag upcoming_clearance deals yet -- that's a
    -- separate, future scanner change (deal rows today only come from a
    -- confirmed clearance/penny hit, see upsert_deal_from_observation) --
    -- these columns exist so the read API and the "Close eye" endpoint are
    -- correct now, at 0 watched deals, instead of needing a second schema
    -- change once the scanner side lands. check_interval is stored as an
    -- INTERVAL for cheap SQL-side halving (see common/db.py's
    -- shorten_check_interval); the API surfaces it as whole seconds.
    deal_kind               TEXT NOT NULL DEFAULT 'active_clearance'
                             CHECK (deal_kind IN ('active_clearance', 'upcoming_clearance', 'penny')),
    check_interval          INTERVAL NOT NULL DEFAULT '4 hours',
    last_checked_at         TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_id, store_id)
);
CREATE INDEX idx_deal_status ON deal (status);

-- Per-store shopping list item state -- one row per deal that's ever been
-- added to a list. Keyed by deal_id, not a fresh (product_id, store_id)
-- pair: `deal` is already UNIQUE(product_id, store_id) (see above), so a
-- list item maps 1:1 onto a deal row and the store comes for free via
-- deal.store_id -- duplicating (product_id, store_id) here would just be a
-- redundant copy of what `deal` already guarantees, with its own
-- uniqueness constraint to keep in sync.
--
-- deal.status='saved' still marks "this deal is on some list" (unchanged,
-- for backward compat with the existing POST /api/deals/{id}/save and
-- History); this table adds the finer-grained per-item state the shopping-
-- list screens (3a/3b) need on top of that. List membership for the lists
-- read endpoint is this table's state != 'no_longer_needed', not
-- deal.status -- see web/backend/queries.py's store_lists.
CREATE TABLE list_item (
    id                 SERIAL PRIMARY KEY,
    deal_id            INTEGER NOT NULL UNIQUE REFERENCES deal(id) ON DELETE CASCADE,
    state              TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open', 'purchased', 'cant_find', 'no_longer_needed')),
    quantity           INTEGER,
    purchased_at       TIMESTAMPTZ,
    -- Free-text, not an enum -- HANDOFF_DEALS_PAGE.md's "Open questions"
    -- leaves the can't-find reason values (gone / mispriced / wrong aisle /
    -- other) undecided by the user. Free-text unblocks the walking view's
    -- "reason" sheet now; narrow to a CHECK'd enum later once that's settled.
    cant_find_reason   TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_list_item_state ON list_item (state);

-- Idempotency + audit for bot alerts — a restart must never re-alert the
-- same deal.
CREATE TABLE alert_sent (
    id                  SERIAL PRIMARY KEY,
    deal_id             INTEGER NOT NULL REFERENCES deal(id) ON DELETE CASCADE,
    channel             TEXT NOT NULL DEFAULT 'telegram',
    sent_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    telegram_message_id BIGINT,
    UNIQUE (deal_id, channel)
);

CREATE TABLE rate_limit_event (
    id           SERIAL PRIMARY KEY,
    retailer_id  INTEGER NOT NULL REFERENCES retailer(id) ON DELETE CASCADE,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type   TEXT NOT NULL CHECK (event_type IN ('403', 'backoff_start', 'backoff_end')),
    detail       TEXT
);

-- Metadata only. Cookies/tokens never touch Postgres — they live solely in
-- the scanner container's browser-profile volume.
CREATE TABLE credential_session (
    id               SERIAL PRIMARY KEY,
    retailer_id      INTEGER NOT NULL REFERENCES retailer(id) ON DELETE CASCADE,
    session_label    TEXT NOT NULL DEFAULT 'default',
    last_verified_at TIMESTAMPTZ,
    status           TEXT NOT NULL DEFAULT 'needs_login'
                      CHECK (status IN ('valid', 'expired', 'needs_login')),
    UNIQUE (retailer_id, session_label)
);

-- Editable overrides for the scanner's env-var config -- lets the
-- dashboard change ZIP/radius/watch filters without a redeploy, per
-- retailer. NULL means "no override, use the env var default" for that
-- field specifically, not "use NULL as the value" -- see common/db.py's
-- get_scanner_settings/upsert_scanner_settings and scanner/main.py's
-- _current_settings for how env + this table merge. Departments-to-watch
-- lives in watched_department below, not here -- explicit selection,
-- not a flat substring-match string.
CREATE TABLE scanner_settings (
    retailer_id              INTEGER PRIMARY KEY REFERENCES retailer(id) ON DELETE CASCADE,
    zip_code                 TEXT,
    radius_miles             DOUBLE PRECISION,
    watch_keywords           TEXT,
    product_list_cache_hours DOUBLE PRECISION,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Explicit departments-to-watch selection (Settings checkbox tree). A row
-- means "this department, and everything under it, is watched" -- the same
-- descendant-inclusion rule as the Deals-tab scope bar's "incl.
-- sub-departments" toggle. No rows for a retailer means "watch everything"
-- (unchanged default from the old flat watched_departments field). See
-- common/db.py's get_watched_department_names for the descendant expansion.
CREATE TABLE watched_department (
    retailer_id    INTEGER NOT NULL REFERENCES retailer(id) ON DELETE CASCADE,
    department_id  INTEGER NOT NULL REFERENCES department(id) ON DELETE CASCADE,
    PRIMARY KEY (retailer_id, department_id)
);

-- Price-check odometer (header wireframe 5b, design-v2) -- a running total
-- of every price check ever performed, plus a cheap short-window rate for
-- the header's "+N in the last minute" line. A dedicated aggregate instead
-- of `SELECT COUNT(*) FROM price_observation` because that table is an
-- ever-growing, unbounded time-series and this gets polled every 2-3s
-- while a scan is running (see web/backend/queries.py's
-- price_check_odometer) -- counting it live doesn't scale. Bumped by
-- common/db.py's increment_price_check_total, called once per
-- successfully-checked product from scanner/orchestrator.py.
CREATE TABLE price_check_counter (
    id            SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton row
    total_checks  BIGINT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO price_check_counter (id, total_checks) VALUES (1, 0);

-- Minute-bucketed counts backing the odometer's "+N in the last minute"
-- line -- a read only ever sums the current + previous bucket row (see
-- price_check_odometer), never a range scan over price_observation.
-- Pruned opportunistically by increment_price_check_total (piggybacked on
-- the same write, not a separate scheduled job) -- meant to stay a
-- handful of rows, never an accumulating history.
CREATE TABLE price_check_rate_bucket (
    bucket_start  TIMESTAMPTZ PRIMARY KEY,
    checks        INTEGER NOT NULL DEFAULT 0
);

-- 'cancelled' status for the header's "Cancel scan" button (wireframe 5b)
-- -- a cooperative cancel (scanner/orchestrator.py's run_scan checks a
-- stop flag at its existing progress checkpoints, see ScanCancelled)
-- closes out whichever scan_run is currently 'running' as 'cancelled'
-- rather than leaving it running forever or misreporting it as 'failed'.
-- Appended as an ALTER here (not edited into scan_run's own CREATE TABLE
-- above) per this branch's shared-file convention -- other agents are
-- editing that table's body in parallel. The DROP/ADD pair below relies
-- on Postgres's default name for an inline, unnamed CHECK
-- ("<table>_<column>_check") -- true by construction on a fresh install
-- (nothing's renamed it since the CREATE TABLE a few lines up in this same
-- script). See docs/schema-changes-design-v2.md for the hand-runnable
-- version against a live database, which looks the name up instead of
-- assuming it, since a live DB's history can't be verified from here.
ALTER TABLE scan_run DROP CONSTRAINT IF EXISTS scan_run_status_check;
ALTER TABLE scan_run ADD CONSTRAINT scan_run_status_check
    CHECK (status IN ('running', 'completed', 'failed', 'backoff', 'cancelled'));
