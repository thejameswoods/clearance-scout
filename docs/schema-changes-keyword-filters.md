# Schema changes — keyword filters (issue #1)

There is no migration framework (see `db/init/001_schema.sql`'s header) -- the
live deployment does not re-run the init script. Run the statements below by
hand against the live database. `db/init/001_schema.sql` has also been
updated in place so a fresh install picks these up automatically.

```sql
ALTER TABLE scanner_settings ADD COLUMN exclude_keywords TEXT;
ALTER TABLE scanner_settings ADD COLUMN keyword_filter_mode TEXT NOT NULL DEFAULT 'simple'
    CHECK (keyword_filter_mode IN ('simple', 'regex'));

CREATE TABLE store_keyword_filter (
    store_id          INTEGER PRIMARY KEY REFERENCES store(id) ON DELETE CASCADE,
    mode              TEXT NOT NULL DEFAULT 'simple' CHECK (mode IN ('simple', 'regex')),
    include_keywords  TEXT,
    exclude_keywords  TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## What this is

A generalization of the existing `scanner_settings.watch_keywords` field
(retailer-wide, include-only, plain-substring product-title filter, already
live as Settings' "Watch keywords" input) into the full request from issue
#1:

- **Excludes, not just includes** — `scanner_settings.exclude_keywords`,
  same comma-separated TEXT format as `watch_keywords`. Checked *after* the
  include list and always wins, so `Includes: "String trimmer", Excludes:
  "Refill"` alerts on a string trimmer without alerting on just its refills
  (the issue's own example).
- **Regex, not just plain substring** — `scanner_settings.keyword_filter_mode`
  (`'simple'` default or `'regex'`), applies to both the include and exclude
  list for that retailer.
- **Store-specific, not just retailer-global** — `store_keyword_filter`, one
  optional row per store. **A row here fully replaces the retailer-wide
  filter for that store — it does not layer/AND with it.** No row means "use
  the retailer-wide filter", same as `scanner_settings` having no row for a
  retailer meaning "use the env-var default". This was a deliberate choice
  over stacking (AND-ing a global filter with a store filter is much harder
  to reason about from the Settings UI, and issue #1 didn't ask for the two
  to compose) — reconsider only if a real use case for layering shows up.

## Where it's applied

Scan-time, same as the existing `watch_keywords` — narrows what the scanner
requests price checks for in the first place (see
`scanner/orchestrator.py`'s `run_scan`, product-listing phase), not a
dashboard display filter like `retailer.min_discount_pct`. Changing a filter
takes effect on the *next* scan of that store, not retroactively on deals
already recorded — same caveat that already existed for `watch_keywords`,
not new here.
