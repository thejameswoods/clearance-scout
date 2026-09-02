# Backlog: upcoming-clearance detection ("Watching")

Recorded per the design-v2 gap-close coordinating brief -- this is the largest
product gap left in the design and belongs in a doc, not a chat log. See
`docs/Clearance Scout Design/clearance-scout/docs/HANDOFF_DEALS_PAGE.md`,
screen 2a's status bar and its per-row "Watching" rendering.

## What the design asks for

Screen 2a's status bar has four tags acting as radio buttons: `Active
clearance`, `Watching`, `Waiting for deeper cut`, `All`. `Watching` means "a
full-price item flagged as heading for clearance" -- the price hasn't dropped
yet, but something (today: nothing) identified it as a candidate. A deal in
that state:

- Renders with a dashed thumbnail border (vs. a solid border for an active
  markdown).
- Shows the full price with `no drop yet` beneath it, instead of a
  struck-through was-price.
- Its store line reads `Flagged as upcoming clearance — price still full.
  Checked every 2h, last 14m ago.` instead of the usual aisle/bay/stock line.
- Gets a neutral `Watching` discount tag instead of a percentage.
- Its `Want` action is replaced by `Close eye`, which tightens the check
  cadence for that one deal.
- Counts toward a `Watching N` tag in the status bar, scoped like every other
  count (retailer/store/department).

## What already exists and works

Everything on the *read and act* side of "Watching" is built and was verified
end-to-end against seeded data during UAT:

- Schema: `deal.deal_kind` (`active_clearance` | `upcoming_clearance` |
  `penny`), `deal.check_interval` (default 4h), `deal.last_checked_at` --
  added in `db/init/001_schema.sql` and documented in
  `docs/schema-changes-design-v2.md` (section 1 as of this pass, after the
  store-hours section was removed and everything renumbered).
- `web/backend/queries.py`'s `status_bar_counts` computes the `watching`
  figure by counting `deal_kind = 'upcoming_clearance'` rows in scope, wired
  into `GET /api/deals/tree` (`web/backend/routes/deals.py`) so the sidebar
  and status-bar counts are correct the moment any row actually has that
  `deal_kind`.
- `POST /api/deals/{id}/close-eye` (`web/backend/routes/deals.py`) halves
  `check_interval`, floored at 15 minutes (`common/db.shorten_check_interval`).
  Usable today on any deal, `upcoming_clearance` or not.
- The entire frontend rendering path for a `Watching` row (dashed thumbnail,
  `no drop yet`, the flagged-status store line, the `Watching` tag, the
  `Close eye` button replacing `Want`) -- built and confirmed rendering
  correctly against seeded `deal_kind='upcoming_clearance'` rows.
- The individual product rescan feature, which the "Checked every 2h, last
  14m ago" line depends on for `last_checked_at` to ever move on an
  already-flagged item: `web/frontend/app.js`'s per-row refresh button posts
  into a queue in `scanner/main.py` (`_refresh_queue`, appended to at line
  270, drained by the loop starting at line 526) that calls
  `refresh_single_product` (`scanner/orchestrator.py:564`). That function
  re-checks one product at every store configured for its retailer, one
  store at a time, reusing the adapter's normal single-item `check_price`
  path.

In short: every piece downstream of "a deal row exists with
`deal_kind='upcoming_clearance'`" is real and tested. Nothing produces that
row.

## What is actually missing

### 1. No write path ever creates a "watching" deal row

`common/db.py`'s `upsert_deal_from_observation` is the only place a `deal`
row is created or updated from a scraped observation, and it gates on the
observation being a confirmed hit:

```python
def upsert_deal_from_observation(conn, product_id: int, store_id: int, observation_id: int,
                                  is_clearance: bool, is_penny: bool) -> tuple[int, bool]:
    ...
    if not (is_clearance or is_penny):
        if existing and existing["status"] in ("new", "active"):
            conn.execute(
                "UPDATE deal SET status = 'stale', latest_observation_id = %s, updated_at = now() WHERE id = %s",
                (observation_id, existing["id"]),
            )
        return (existing["id"], False) if existing else (None, False)
    ...
```

A full-price observation (`is_clearance=False, is_penny=False`) either marks
an *existing* deal stale, or -- for a product that has never had a deal row
-- does nothing at all. No `deal` row is ever created for a still-full-price
item. Since `deal_kind='upcoming_clearance'` is a column *on* `deal`, and
there is no deal row, there is nothing to mark as watching. The `Watching N`
count in `status_bar_counts` is correctly wired but will read 0 forever
under the current write path, exactly as flagged in
`docs/schema-changes-design-v2.md` section 1.

This is the first of two gaps, and it's purely a decision about *when* to
open a deal row, not a schema or read-path problem.

### 2. No signal identifies "heading for clearance" while still full price

Opening a deal row on every full-price observation would create one for
every product ever checked -- clearly not the intent ("Watching" is supposed
to be a flagged subset, not the whole catalog). Something has to decide
*which* full-price items are worth watching. I looked at what
`adapters/home_depot/` already fetches to see if such a signal exists today.

**Finding: no such signal is available in what the adapter already
fetches.** The two GraphQL queries that carry pricing
(`adapters/home_depot/api_client.py`):

```graphql
query mediaPriceInventory($itemIds: [String!]!, $storeId: String!) {
  products(itemIds: $itemIds) {
    itemId
    pricing(storeId: $storeId) { value original clearance { value dollarOff percentageOff } }
    fulfillment(storeId: $storeId) { fulfillmentOptions { ... } }
  }
}
```

and the single-item `productClientOnlyProduct` query, both request the same
shape: `pricing.value`, `pricing.original`, and `pricing.clearance` (only
non-null once a markdown is *already* live), plus fulfillment/inventory.
`adapters/home_depot/clearance.py`'s `detect_clearance` -- the only clearance
logic in the codebase -- derives "is this on clearance" entirely from
`pricing.clearance` being non-null and an "advertised" (BOPIS-pulled)
fulfillment signal. Both of those only exist *after* Home Depot's own system
has already applied a markdown. There is nothing in this payload that
predicts a markdown before it happens -- no discontinued/closeout flag, no
"scheduled markdown" field, no inventory-velocity or age-of-listing signal,
nothing resembling a trend.

I'm not asserting Home Depot's API has no such signal anywhere -- only that
it's not present in the two queries this adapter already calls. Finding one
(if it exists) would mean probing undocumented parts of their GraphQL
schema, the same way `clearance.py`'s header documents the existing
clearance/penny signals were found (by reading HDScanner's source and
confirming live against real SKUs). That's real scraper R&D, not a config
change to an existing query.

## Implementation sketch

Two independent halves, and the second is the one with actual uncertainty:

1. **Open a deal row for a full-price item once something flags it.**
   `upsert_deal_from_observation`'s early-return branch would need a third
   caller-supplied signal (e.g. `is_upcoming_clearance: bool`, defaulting to
   `False`) that, when true, creates or updates the deal row with
   `deal_kind='upcoming_clearance'` instead of returning early. This is a
   small, mechanical change once gate #2 below produces something to pass in.

2. **Decide what sets that flag.** Candidates, roughly in order of how much
   new scraping they need:
   - **Keyword/category heuristic** (no new scraping): user-maintained watch
     list of specific SKUs or a department/keyword filter ("always watch
     anything in Building Materials > Lumber over $50"), applied client- or
     scan-side to items already being checked at full price. Cheapest to
     ship, but it's a manual watch list, not real "heading for clearance"
     detection -- closer to a saved search than the design's intent.
   - **Adapter-level signal, if one exists** -- would require the R&D
     described above before any implementation estimate is honest.
   - **Statistical/historical signal**: flag a product if its price has been
     stable a long time at a store where the same product went on clearance
     before, or based on some other pattern in `price_observation` history.
     Buildable with existing data, but its accuracy is unproven and it's a
     meaningfully bigger piece of work than the other two options.

## Risks

- **Deal-row volume.** `deal` today only ever holds confirmed hits, which is
  why it's cheap to scan and count. Opening it to any full-price item that
  passes a broad gate (especially the keyword/category heuristic, which has
  no natural ceiling) risks turning `deal` into a near-copy of `product`,
  degrading every query in `web/backend/queries.py` that scans it (`list_deals`,
  `status_bar_counts`, `department_tree_with_counts` all do full or
  near-full scans of open deals per request). Whatever gate ships needs an
  explicit, deliberately narrow scope -- start with an opt-in watch list, not
  a blanket rule, and measure row growth before widening it.
- **`check_interval` is stored but nothing schedules from it.** `deal.
  check_interval` and `deal.last_checked_at` exist and `close-eye` correctly
  halves the former, but no scan loop actually reads `check_interval` to
  decide when to re-check a given deal -- `scanner/orchestrator.py`'s normal
  scan cadence is department-level, not per-deal. Today "last checked"
  advances only when a full department scan happens to reach that product,
  or when someone manually hits the per-product refresh
  (`refresh_single_product`). A real "watching" feature implies re-checking
  watched items on their own cadence, independent of the full department
  scan schedule -- that scheduler doesn't exist yet and is its own piece of
  work, separate from both gaps above.
