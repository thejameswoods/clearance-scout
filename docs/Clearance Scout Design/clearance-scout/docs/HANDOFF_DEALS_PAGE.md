# Handoff: Deals page — multi-store, tree-structured departments + shopping lists

## Overview

The Deals page and header of a personal clearance aggregator (repo: `thejameswoods/clearance-scout`, branch `main`).
A scraper detects clearance/penny items across retailers and stores; a single human user monitors the
feed, dismisses most items, and turns the interesting ones into per-store shopping lists used inside
the physical store.

Three things this expands beyond the current implementation:

1. **Multiple retailers, each with many stores.** The current UI has a flat store `<select>`. The new
   model is retailer → store (Home Depot → #3612, #3608; Best Buy → #1188).
2. **Tree-structured departments, per retailer.** Each retailer has its own tree of arbitrary depth
   (`Building Supplies / Lumber / Dimensional / Pressure Treated`;
   `Video Games / Consoles / PlayStation 5`). A department holds child departments and items. Selecting
   a department shows deals for it **and everything downstream**; drilling further is optional refinement.
3. **This is not e-commerce.** Deals are triaged (want / not interested / not yet), then shopped in
   person: drive to a store, walk the aisles, mark found / can't find / no longer needed.

## About the design files

The files in this bundle are **design references created in HTML** — wireframe prototypes showing
intended structure, content and behavior. They are not production code to copy.

The task is to **recreate these designs in the target codebase's existing environment**, using its
established patterns and libraries. The existing app is a plain static frontend
(`web/frontend/index.html` + `app.js` + `style.css`, no framework, no build step) served alongside a
Postgres schema in `db/init/`. Unless the team decides to introduce a framework, implement this in the
same vanilla HTML/CSS/JS structure and extend `style.css` rather than importing a new design system.

`Deals Wireframes.dc.html` is authored in a component format that needs a runtime; **open it in a
browser only for reference**. `Deals - Current UI.dc.html` is a recreation of the app as it exists
today, included as the before-picture.

## Fidelity

**Low-fidelity.** These are wireframes: structure, hierarchy, content and interaction model are the
deliverable. Spacing and type are deliberately utilitarian. Apply the app's own visual language
(`web/frontend/style.css`) for styling — do not chase the wireframes' exact pixels or colors. Where the
wireframes and the current stylesheet disagree on a purely visual matter, the stylesheet wins. Where
they disagree on **structure, labels, or behavior**, the wireframes win.

The wireframes were drawn on a wireframe design system ("Industry": steel-blue `#5980a6` accent on a
`#f2f2f3` ground, Barlow Condensed headings over Barlow, square corners, hairline borders). Treat that
as wireframe styling, not a brand spec.

## Screens / Views

There are nine views. The first four (browse + shopping lists) are the core ask. The next three
(item detail, Scan Now, Settings) are follow-on refinements — see the note on each about how close
they are to current product scope. The last two are the header redesign.

---

### 1. Deals browse — desktop (wireframe id `2a`)

**Purpose.** The daily triage surface. The user scans deals in a chosen scope and gives each one of
three dispositions: want, not interested, or not yet.

**Layout.** Full-width page. Top: existing app header + tab bar (`Deals · Lists · History · Scanner ·
Settings`; the current app also has Browser/Logs tabs — keep whatever the app has). Below the header, a
two-pane row filling the viewport height:

- **Left sidebar, fixed 252px, 1px right border.** Two stacked sections.
- **Right pane, flex 1.** Stacked bars, then the deal list.

**Sidebar — section 1: RETAILERS & STORES.**
Small monospace uppercase section label. Then a tree, one row per line, each row `justify-content:
space-between` with a monospace open-deal count on the right:

```
▾ Home Depot                104     <- retailer, expanded, selected (accent tint bg, bold)
    All 2 stores            104     <- selected scope, accent-colored text
    Chapel Hill #3612        61
    Cary #3608               43
▸ Best Buy                   44
▸ Lowe's                      0     <- muted when zero
```

The retailer is the root. "All N stores" is a real selectable scope — it is the default when a
retailer is picked. Expanding/collapsing a retailer is independent of selection. Counts are open
(untriaged) deals in that scope.

**Sidebar — section 2: `<RETAILER>` DEPARTMENTS.**
Label names the current retailer ("HOME DEPOT DEPARTMENTS"), because the tree is retailer-specific —
switching retailer replaces this whole panel. A text input, "Filter departments", filters the tree by
name. Then the department tree, arbitrary depth, indent 14px per level, disclosure `▾`/`▸`, each row
with a right-aligned monospace count:

```
▾ Building Materials         72
   ▾ Lumber                  48
      ▾ Dimensional          31    <- selected: accent tint bg, bold
         Pressure Treated    23
         Kiln-Dried            8
      ▸ Plywood & Sheet      17
   ▸ Drywall                 14
▸ Electrical                 19
▸ Tools                      13
```

Leaf departments have no disclosure marker. A note under the tree in the wireframe explains the
retailer-specific behavior; that note is documentation, not UI — omit it.

**Right pane — scope bar.** Breadcrumb of the current selection, from retailer through the department
path, last segment bold:
`Home Depot · all stores / Building Materials / Lumber / Dimensional`
followed by a small outlined toggle-tag: `incl. sub-departments ✓`. This toggle is **on by default**
and is the mechanism for "downstream departments are optional in viewing" — off, the list shows only
deals whose department is exactly the selected node.

**Right pane — search/sort row.** Full-width search input ("Search these deals…", min-width 220px),
then a sort select (newest / oldest / biggest discount / lowest price / most stock), then a filters
select or popover. Filters carry over from today's app: clearance only, penny only, min % off, price
range, in-stock only.

**Right pane — status bar.** Label `STATUS`, then filter tags acting as radio buttons:

| Tag | Meaning |
| --- | --- |
| `Active clearance 24` (selected: solid accent) | Price is currently marked down |
| `Watching 7` | Flagged as *upcoming* clearance; price still full |
| `Waiting for deeper cut 5` | User said "not yet" with a threshold |
| `All 31` | Everything untriaged |

Right side of the same bar, muted: a statement of link behavior —
`Titles and images open the store's product page (?store=cheapest)`. In the built version this is
better as a tooltip or omitted.

**Right pane — "new" bar.** Accent-tinted strip: `**9 new** since Aug 30 · 31 total`, a
`Triage new only` button that scopes the list to deals detected since the last visit, and on the right
`Last dismissed: <name> · undo`.

**Right pane — deal rows.** One row per **product** (not per store-price). Row is a flex line,
~11px vertical padding, 1px bottom border, columns left to right:

1. **Thumbnail** — 52×52, 1px border, no radius. It is a link (see Links below). Dashed border when
   the deal is in `Watching` state (no price drop yet).
2. **Body, flex 1, min-width 0** — three lines:
   - Product name, 600 weight, **a link**, followed by a small muted `↗ #3612` showing which store
     the link targets.
   - `<leaf department> · SKU <sku>`
   - Store line. Multi-store: `▾ 2 of 2 Home Depot stores` in accent color, clickable to expand.
     Single store: `▸ Chapel Hill #3612 · Aisle 12 / Bay 007 · 26 left`.
     For a `Watching` deal, this line instead reads
     `Flagged as upcoming clearance — price still full. Checked every 2h, last 14m ago.`
     For a `Not yet` deal: `Waiting for ≥80% off — now 55%. Re-alerts at $1.79 or lower.`
3. **Price, 104px, right-aligned** — current price 600 weight 15px; below it the was-price struck
   through and muted. Multi-store shows a range (`$3.47 – $5.12`). `Watching` shows the full price
   with `no drop yet` beneath. Penny items show `$0.01` and no was-price.
4. **Detected, 96px** — relative on line 1 in text color and 600 weight (`2h ago`, `yesterday`,
   `3d ago`), absolute on line 2 muted (`Aug 31 · 4:12p`). **This is a requested addition — every deal
   must show when it was detected.**
5. **Discount tag** — accent tag with the percentage (`71%`, range `62–71%` when multi-store);
   outlined `Penny` for penny items; neutral `Watching`; neutral `Not yet · 80%`.
6. **Actions, no shrink** — see Dispositions below.

**Expanded multi-store rows.** Clicking the `▾ N of N stores` line reveals one indented line per store
(left padding aligns under the body, ~80px), each a subtle neutral fill:

```
Chapel Hill #3612 ↗   Aisle 12 / Bay 004 · 4 left    detected 2h ago   $3.47   [Add to this store's list]
Cary #3608 ↗          Aisle 09 / Bay 011 · 26 left   detected 3h ago   $5.12   [Add to this store's list]
```

Store name is a link to that store's product page. Per-store detected time is shown separately from
the row's roll-up. Each line has its own **Add to this store's list** button — this is how a
multi-store deal reaches the right list.

**Footer of the list.** `28 more · J/K move · W want · D never show again` — keyboard shortcuts on the
focused row.

---

### 2. Deals browse — mobile (wireframe id `2c`)

**Purpose.** The same triage, done on a phone, one thumb, high volume.

**Layout.** 390px viewport. Sticky header, scrolling list, fixed bottom tab bar.

**Header.**
- Title row: `DEALS` (condensed, 18px) and `31 · 9 new` on the right.
- Retailer chips, horizontally scrollable, one per retailer with count; selected chip is solid accent.
  `Home Depot 104 | Best Buy 44 | Lowe's 0`
- Scope bar: a bordered row with the truncated department path and store scope
  (`Lumber › Dimensional · all stores`) and an `Edit` affordance on the right, opening a full-screen
  tree picker (not drawn — reuse the desktop tree, full width, with the retailer at the root).
- Swipe legend, 11px muted, left and right aligned:
  `← left: dismiss · long-swipe left: not yet` / `right: add to list →`

**List.** Departments become sticky section headers: neutral fill, monospace uppercase,
`PRESSURE TREATED · 23`. Under each, item rows: 64×64 thumbnail, then name (2 lines max), then a
baseline-aligned price + discount tag, then a third line `#3612 · Aisle 12/004 · 4 left`, or
`2 stores · pick store on add` for multi-store.

**Swipe.** A **single continuous gesture**, not swipe-then-tap. The colored panel revealed behind the
row is a trail, not a button — nothing on it is tappable. The wireframe deliberately shows two rows
frozen mid-swipe, labeled "Adding — mid-swipe" and "Dismissing — mid-swipe", to document the visual:

- **Right swipe** — accent fill revealed at the **left** edge with `＋ Adding`. Committing adds to the
  store's list. Multi-store items need a store: either a sheet with the store options (each showing
  price and aisle) or a silent default to the cheapest, with the choice surfaced in the undo toast.
  **Open decision — confirm with the user.**
- **Short left swipe** — neutral fill revealed at the **right** edge with `× Dismissing`. Commits to
  "never show this product again".
- **Long left swipe** — opens the Not-yet threshold sheet (same options as desktop).

A row already in "not yet" state renders muted with a dashed thumbnail and
`Not yet — re-alert at 80% off (now 55%)`.

**Footer of the list.** `Dismissed 2 · 1 waiting for a deeper cut · undo`.

**Bottom tabs.** `Deals · Lists 3 · History · Scanner`, active tab underlined in accent.

---

### 3. Shopping lists — desktop (wireframe id `3a`)

**Purpose.** Plan the driving. **One list per store, always separate — lists never merge.** There is no
"trip" object; a store list *is* the trip.

**Layout.** Page header + tab bar with `Lists` active. Below it:

- **Summary bar.** `3 STORE LISTS · 14 ITEMS` in condensed type, then one outlined tag per store list
  with its count (`Home Depot #3612 · 6`), then on the right an **Email all 3 lists** button and the
  muted note `Lists never merge — each is a separate trip.`
- **Two-column grid of store list cards**, 1px dividers between cells. A store with no items has no
  card. Overflow stores can render collapsed (see below).

**Store list card header.**
- Store name in condensed caps: `HOME DEPOT · CHAPEL HILL #3612`, item count right-aligned.
- Address line, then hours/proximity: `Open today 6a–10p · closes in 4h`, or `Open today 6a–10p · 24
  min away` for a store that isn't the nearest. **Store hours, address and a directions link are
  required content.**
- Button row: `Directions ↗` (opens maps), `Open walking view`, **`Email this list`** (primary),
  `Print`.
- Under the first card, an explanatory line documents the email payload:
  `Email includes aisle/bay, photos, prices, SKUs and the directions link — readable with no signal in
  the store.` **This is the offline story: many stores have weak cell signal, so the emailed copy is
  the in-store fallback.** The email must be self-contained — inline the item photos or accept their
  absence, no auth-gated links, plain formatting that survives a mobile mail client, one section per
  aisle in the same order as the walking view, and the maps URL as a visible link.

**Store list body — grouped by aisle number, ascending.** Group header: neutral fill, monospace
uppercase, `AISLE 09 · 1 ITEM`. Items with no aisle from the scraper collect in a final
`AISLE UNKNOWN · 2 ITEMS` group.

Item row: checkbox, 40×40 thumbnail, name + `Bay 003 · 140 left · SKU 1000556677`, then price and an
optional quantity (`×4`). Three visible states:

| State | Rendering |
| --- | --- |
| Open | Normal, unchecked |
| Found & purchased | Checked, name struck through, thumbnail 50% opacity, `Bay 004 · found & purchased 11:20a` |
| Can't find | Neutral fill row, dashed empty thumbnail, `Bay 007 · marked **can't find** · kept for next trip` |

Items marked can't-find stay on the list. Purchased items stay visible until cleared.

**Store list card footer.** `1 of 6 picked up · 1 not found` with `Clear finished` on the right, or
`Nothing picked up yet`.

**Collapsed store list.** A single row: store name in condensed caps, then
`3 items · <address> · closes 8p`, then `Email this list` and `Expand` on the right.

---

### 4. Shopping list — mobile walking view (wireframe id `3b`)

**Purpose.** In the store, walking the aisles. One store only. Aisle order. Large targets.

**Layout.** 390px, header / scrolling list / fixed action bar.

**Header.** `‹ Lists` back link and `4 of 6 left`; store name over two condensed lines
(`HOME DEPOT` / `CHAPEL HILL #3612`); `2540 E Franklin St · closes 10p · Directions ↗`; a 4px accent
progress bar (fraction resolved).

**Current aisle — promoted card.** The next unresolved item gets an accent-tinted header
`AISLE 09 · BAY 003` and a large card: 84×84 photo, name at 15px, price 17px with a discount tag,
then `140 left · yellow-tag clearance` and `SKU 1000556677`. **All of these are required in-aisle:
aisle/bay, photo, price and expected discount, SKU, stock quantity, and why it was flagged.**

Action row, full width, targets ≥44px tall:
`[ Found it ]` primary and flexing, `[ Can't find ]`, `[ Skip ]`.

**Remaining aisles.** Compact rows under neutral group headers (`AISLE 12 · 3 ITEMS`): 56×56 thumb,
name, `Bay 002 · $2.98 · ×10`. Resolved items render in place — can't-find as a dashed muted row with
a `reason` link, purchased at 55% opacity, struck through, `purchased 11:20a · $3.47`. A trailing row
links to `Aisle unknown · 2 items ▸`.

**Fixed bottom bar.** `No longer need`, `Undo`, and `1 purchased` on the right.

**Not drawn — needs design or a developer decision:** the can't-find reason sheet (gone / mispriced /
wrong aisle / other), the multi-store add sheet on mobile, and the finished-trip summary.

---

### 5. Item detail (wireframe id `4a`)

**Purpose.** Replaces the current product-detail modal, which is too small to read (cramped table,
horizontal scrollbar cutting off columns). This is a **greenfield rewrite**, not a tweak.

**Layout.** A large overlay (not the current small modal) — roughly 860px wide, scrollable, dimmed
backdrop. Breadcrumb + back link + close at the top: `‹ Back to Deals · Home Depot / Building
Materials / Lumber / Dimensional / Pressure Treated`.

**Header block.** 160×160 photo, then: status tag (`Active clearance`, solid accent) plus, when
applicable, a secondary tag noting prior watch time (`Was on close-eye watch 3d`); product name at
26px; SKU and retailer product id; price line with range, struck-through list price and a discount
tag; the disposition actions (`Want`, the `Not interested` / `▾ Not yet` split button, `Share link`,
`Email to self`) — same semantics as the browse row.

**History block.** A small sparkline of price over time plus a one-line narrative:
`Full price $11.98 since Aug 20 · flagged upcoming clearance Aug 26 (checked every 2h) · dropped to
$5.12 (62%) Aug 29 · deepened to $3.47 (71%) 2h ago at Chapel Hill #3612.` This is the
upcoming-clearance / close-eye timeline made visible at the item level, not just a tag.

**Per-store cards**, one per store carrying the item (not a cramped table column): store name as a
link (`Chapel Hill #3612 ↗`, opens that store's product page), a `cheapest` badge where relevant,
address, aisle/bay, stock dot + count, last-checked time, this store's price/was-price/discount, and
an `Add to this store's list` button. This is the same per-store data the browse row's expanded state
shows (see screen 1), just given room to breathe.

### 6. Scan Now scoping (wireframe id `4b`)

**Purpose.** Today, `Scan Now` triggers one unscoped scan across every configured retailer, store and
department (`POST /api/scan/trigger`, no parameters) — with a few stores configured this already runs
hours. This screen lets the user pick a subset before triggering.

**Layout.** A ~560px dialog. Subtitle explains the problem: *"A full scan across every configured
retailer and department can take hours. Narrow it down to what you need before you drive out."*

One block per retailer: a header checkbox (select-all-stores-for-this-retailer) with an
"N of M stores selected" count, then one checkbox row per store (`Chapel Hill #3612 · 6 mi · scanned
2h ago`), then a summary line naming the department scope in effect with an `Edit` link into the
Settings tree (screen 7). A retailer with no stores yet (`Lowe's`) shows disabled with
`not connected — add in Settings`.

Footer: a live estimate (`Estimated ~24 min · 2 stores · 6 departments`) that recomputes from the
current selection, then `Cancel` / `Start scan`.

**Backend implication.** `/api/scan/trigger` needs to accept `store_ids` (and optionally
`department_ids`) and scan only those; `scan_run` is already keyed by `retailer_id` so scoping to a
store subset within a retailer is the main gap. The time estimate needs a per-department or
per-store average duration to compute from — a rough constant is fine to start.

### 7. Settings, per retailer (wireframe id `4c`)

**This screen is ahead of where the product is today — treat it as a future-state placeholder, not
near-term scope.** It designs the target shape (per-retailer config, per-store toggles, a live
department tree) for when the product is ready to support multiple independently-configured
retailers; it is not a request to build all of it now. The wireframe itself carries this same caveat.

**Why it's ahead:** `scanner_settings` today is a single global row (`id=1`, `CHECK (id = 1)`) shared
by every retailer, and the retailer list itself is fixed via the `RETAILERS` env var, not editable in
the UI. Real per-retailer settings need a `scanner_settings` row (or table) per retailer, plus write
endpoints for enabling/disabling a retailer and its stores.

**Layout, once it's real.** Left nav lists retailers (name, store count, enabled state) plus a
GLOBAL section (scan schedule, notifications, data export); an `+ Add retailer` affordance. Right
panel, per selected retailer:

- Header: name, connection/adapter status, an `Enabled` toggle, `Remove retailer`.
- **Location & stores** — zip + radius, and the discovered store list with a checkbox per store to
  include/exclude it from scans.
- **Departments to watch** — the flat `watched_departments` text field is replaced by a **checkbox
  tree**: a filter input, then the same disclosure tree as screen 1's sidebar but with a checkbox per
  node. A parent with some-but-not-all children checked shows a dash (–) instead of a checkmark. A
  summary line states the explicit vs. inherited count (`2 selected explicitly · 25 products in scope
  incl. sub-departments`). Selecting a parent implicitly includes everything downstream, same rule as
  screen 1's `incl. sub-departments` toggle.
- **Watch keywords** — stays a flat text field; keywords aren't hierarchical.
- **Schedule** — scan interval, scan-on-startup.
- Danger zone: `Force product re-list` (same semantics as today), `Save changes`.

### 8. Header — detailed scan status + cancel (wireframe id `5b`, chosen over `5a`)

**Purpose.** Today's header shows a static badge (`checking…`) and a `#scan-progress-detail` span that
is mostly empty. The user wants to see phase, progress and be able to cancel a long scan, without a
persistent second row cluttering the header when idle.

**Layout — two rows, second row conditional.** Row 1 is the existing nav (brand, tabs, `Scan now`),
unchanged. Row 2, a status bar, **renders only while `scanner.state === "scanning"`** and disappears
entirely when idle — it is not a permanently-reserved strip. While visible it shows: a pulsing dot,
phase breadcrumb (`Home Depot › Cary #3608 › Lumber / Dimensional`), `142 of 310 products · ~6 min
left`, a thin progress bar, and a `Cancel scan` button. When idle, row 1 alone shows a quiet running
total (`1,482,916 checks · last scan 2h ago`) next to `Scan now`.

**The odometer.** A running counter of total price checks ever performed, styled as a mechanical
digit counter (each digit its own bordered cell, tabular numbers) — big and legible while scanning
(in the status bar, right-aligned), small and quiet in the idle nav row. Digits that just rolled over
highlight in the accent color, and a `+37 in the last minute` line under it while scanning reinforces
that it updates live, not just on page load.

**Backend implications — this is new, not just UI:**
- No price-check counter exists today. Needs an incrementing counter (e.g. a `price_check` count on
  `scan_run`, or a dedicated aggregate row) that the frontend can poll.
- `#scan-state-badge` / `#scan-progress-detail` today update on a 15s poll (`refreshScanStatus`,
  `setInterval(refreshScanStatus, 15000)`). A ticking odometer and a responsive progress bar want a
  tighter loop — poll faster (e.g. 2–3s while scanning) or move to SSE/websocket push.
- `Cancel scan` needs a real cancellation path into the scanner process — `/api/scan/trigger` has no
  corresponding cancel endpoint today.

## Interactions & behavior

### Dispositions — the core interaction

Every deal in the feed gets exactly one of four dispositions. Note that **"not interested" is the
default action and "not yet" is the exception path** — hence the split button.

| Disposition | Desktop control | Result |
| --- | --- | --- |
| Want | `Want` primary button | Deal enters that store's shopping list. Multi-store: use the per-store `Add to this store's list` button on the expanded rows |
| Not interested | `Not interested` — the full-width half of a split button | **Never show this product again**, at any store |
| Not yet | The narrow `▾` caret attached to the right edge of `Not interested` | Opens the threshold panel below |
| Close eye | Replaces `Want` on `Watching` rows | Raises the price-check frequency for a flagged-but-not-yet-discounted item |

**The split button.** Two `.btn-secondary` elements flush against each other in a flex row: the
`Not interested` half has `border-right: none`; the caret half is `▾` with ~7px horizontal padding and
a title of "Not yet — re-alert at a deeper discount". When the panel is open the caret is filled with
the primary style.

**The Not-yet threshold panel.** A bordered popover, ~330px, right-aligned under the row, with a
medium shadow. Label: `NOT YET — TELL ME AGAIN WHEN…`. Four radio options:

1. `Discount reaches [85% ▾] or better` — select of discount steps
2. `Price drops below [$3.00]` — text input
3. `It hits penny status`
4. `Never — hide this product for good`

Actions `Set threshold` (primary) and `Cancel`. Footnote:
`Row leaves the feed and returns as new only if the threshold is met — at any store where it's met.`

Setting a threshold removes the deal from the active feed and files it under
`Waiting for deeper cut`. When a later scrape satisfies the threshold at **any** store, the deal
re-enters the feed as new. A `Not yet` row's actions are `Change` and `Never`.

### Links out to the retailer

- Product **title** and **thumbnail** link to the retailer's product page with the detected store as a
  query parameter: `?store=<storeNumber>`.
- When several stores have the item, the title/thumbnail link targets the **cheapest** detected store;
  fall back to the first detected store if prices tie or are unknown. The chosen store is shown next
  to the title as a muted `↗ #3612`.
- A **store name** on an expanded per-store line links directly to that store's product page.
- Open in a new tab.

### Other behavior

- **Scope changes** (retailer, store, department, `incl. sub-departments`) re-query the feed and should
  be reflected in the URL so a scope is linkable and survives reload.
- **Selecting a retailer** swaps the department tree wholesale; do not attempt to reconcile trees
  across retailers.
- **Counts** on every tree node and status tag are open (untriaged) deal counts within that scope,
  including downstream departments.
- **Undo** is available for every disposition, surfaced in the "new" bar on desktop and as a footer
  line / toast on mobile.
- **Keyboard**, desktop: `J`/`K` move the focused row, `W` want, `D` never show again. Add `N` for the
  Not-yet panel if it fits.
- **Detected time** is relative up to a week, then absolute; the absolute timestamp is always visible
  as the second line.
- **Print** on a store list produces the same aisle-ordered content as the email.

## State management

Deal-level state (server-side, per product+store):

- `detected_at` — required on every deal, drives both the Detected column and the "new since" bar.
- `deal_kind` — `active_clearance` | `upcoming_clearance` (the "watching" flag) | `penny`.
  The existing schema's clearance/penny flags cover part of this; upcoming-clearance needs its own
  flag plus a check cadence and `last_checked_at`.
- `check_interval` / `last_checked_at` — shown as `Checked every 2h, last 14m ago`; `Close eye`
  shortens the interval.
- `status` — extends the current `deal.status`: `new` | `saved`(want) | `bought` | `dismissed`
  (permanent, product-level) | `deferred` (not yet).
- `defer_rule` on a deferred deal — `{ type: 'discount_pct' | 'price' | 'penny', value }`, evaluated
  against every store on each scrape; satisfying it flips the deal back to `new`.
- Dismissals are **product-level, permanent, across stores** — not per-deal.

List/trip state:

- A shopping list is keyed by **store**, never by retailer or trip.
- List item state: `open` | `purchased` (with timestamp) | `cant_find` (with an optional reason) |
  `no_longer_needed` (removed from the list, does not re-dismiss the product).
- Optional quantity per list item.
- Aisle/bay come from the deal record and may be null → the `Aisle unknown` group.

Client state:

- Current scope: retailer, store-or-all, department node, include-descendants flag.
- Tree expansion state, per retailer, persisted.
- Department filter text.
- Search / sort / filters / status tab.
- Last-visit timestamp for the "N new" bar.
- Undo stack (last disposition).
- Mobile: in-flight swipe offset and commit threshold.

## Design tokens

None to lift — the wireframes are lofi. Use `web/frontend/style.css`. The wireframe values are recorded
only so nothing reads as arbitrary:

- Accent `#5980a6`, ground `#f2f2f3`, text `#1d1f20`, with 100–900 neutral and accent ramps.
- Barlow Condensed 600/700 for headings and store names; Barlow 400/600 for body; a monospace stack for
  counts, timestamps, SKUs and section labels.
- Square corners (4px max), 1px hairline borders, no fills on cards.
- Type sizes in use: 10–11px monospace labels, 11.5–12px secondary text, 13–13.5px row body,
  15–17px prices, 18–21px condensed headings. Mobile item names 13.5px, in-aisle name 15px.
- Row padding 11px vertical / 16px horizontal; sidebar 252px; thumbnails 40 / 52 / 56 / 64 / 84px.
- Hit targets in the walking view ≥44px.

## Assets

None. Every image is a hatched placeholder box; real product photos come from the scraper. Icons in the
built version should come from whatever the app already uses (the wireframes use text glyphs
`▾ ▸ ↗ ＋ × ‹ ›` as stand-ins).

## Files

In this bundle:

- `Deals Wireframes.dc.html` — all nine wireframes. Turn 2 (`2a`, `2c`) is browse/triage; turn 3
  (`3a`, `3b`) is the shopping lists; turn 4 (`4a`, `4b`, `4c`) is item detail, Scan Now scoping and
  per-retailer Settings; turn 5 (`5a`, `5b`) is the header redesign — `5b` is the chosen direction.
  Rendering requires the component runtime; read it as reference.
- `Deals - Current UI.dc.html` — the app's Deals tab as it exists today, for comparison.
- `web/frontend/style.css` — the app's current stylesheet, copied from the repo unmodified.
- `github.md` — repo, branch, and which repo files each design was derived from.
- `screenshots/` — one PNG per wireframe (`2a-deals-desktop.png` … `5b-header-twobar.png`), for
  viewing without opening the `.dc.html` runtime.

In the repo, the code this replaces or extends:

- `web/frontend/index.html` — the Deals tab markup, filter bar, deal table, detail modal.
- `web/frontend/app.js` — filter/sort/fetch logic, the root+leaf department dropdowns, the flat store
  select, save/bought/dismiss calls.
- `web/frontend/style.css` — all current styling.
- `db/init/001_schema.sql` — `retailer`, `store`, `department` (with `parent_department_id`), `product`,
  `deal`. The department tree and multi-store model already exist here; the frontend has not exposed
  them.

## Open questions for the user

1. Mobile right-swipe on a multi-store deal: store-picker sheet, or silent default to cheapest?
2. Can't-find reasons: which values (gone / mispriced / wrong aisle / other)?
3. Should purchased items auto-clear from a list after some time, or only on `Clear finished`?
4. Screen 7 (Settings) is a future placeholder, not scoped for this pass — confirm before building it.
5. Screen 6 (Scan Now) needs `/api/scan/trigger` to accept a store/department scope — confirm that's
   in scope for this pass alongside the UI.
6. Screen 8 (header) needs a new price-check counter and a cancel-scan endpoint — confirm both are in
   scope, or ship the status bar/progress UI first and stub the odometer and Cancel until they land.
