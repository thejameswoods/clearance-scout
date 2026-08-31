-- Clearance Scout — retailer-agnostic schema.
-- Every table keys off retailer_id so a second adapter (e.g. Lowe's) needs
-- zero schema changes, only a new `retailer` row.

CREATE TABLE retailer (
    id              SERIAL PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,       -- e.g. 'home_depot'
    display_name    TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    adapter_version TEXT NOT NULL DEFAULT '0'
);

CREATE TABLE store (
    id                 SERIAL PRIMARY KEY,
    retailer_id        INTEGER NOT NULL REFERENCES retailer(id) ON DELETE CASCADE,
    retailer_store_id  TEXT NOT NULL,           -- the retailer's own store number
    zip_code           TEXT NOT NULL,
    name               TEXT,
    address            TEXT,
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
                            CHECK (status IN ('new', 'active', 'stale', 'saved', 'bought', 'dismissed')),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_id, store_id)
);
CREATE INDEX idx_deal_status ON deal (status);

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
