repo: thejameswoods/clearance-scout
branch: main

## Last sync

date: 2026-09-02T00:00:00Z

### Updated in this project

- Recreated the current Deals tab (filters, deal table, product-detail modal) from the live frontend.
- Added nine wireframes: Deals browse (desktop/mobile), shopping lists (desktop/mobile walking view),
  a greenfield item-detail overlay, a scoped Scan Now dialog, a per-retailer Settings tab with a
  department checkbox tree (future placeholder), and a header redesign with a live scan status bar,
  cancel, and a running price-check odometer.
- Confirmed against `db/init/001_schema.sql` and `web/frontend/app.js` that per-retailer scan config,
  scoped `/api/scan/trigger`, a price-check counter and a cancel-scan endpoint are backend gaps, not
  just UI gaps — noted in the handoff doc.
- Packaged the handoff doc + wireframes + screenshots into `clearance-scout/design` and
  `clearance-scout/docs` for the dev team (no push access from this environment — user moves these
  into the repo).

## Screen map

| Screen | Built from |
| --- | --- |
| Deals — Current UI.dc.html | web/frontend/index.html, web/frontend/app.js, web/frontend/style.css |
| Deals Wireframes.dc.html (2a/2c, 3a/3b) | db/init/001_schema.sql, web/frontend/app.js, README.md |
| Deals Wireframes.dc.html (4a item detail) | web/frontend/app.js (openProductDetail), web/frontend/index.html modal |
| Deals Wireframes.dc.html (4b Scan Now) | web/frontend/app.js (scan-now-btn, /api/scan/trigger), db/init/001_schema.sql (scan_run) |
| Deals Wireframes.dc.html (4c Settings) | web/frontend/app.js (renderSettings, /api/settings/*), db/init/001_schema.sql (scanner_settings) |
| Deals Wireframes.dc.html (5a/5b header) | web/frontend/index.html (.topbar), web/frontend/app.js (refreshScanStatus, scan-state-badge) |
| web/frontend/style.css (copied) | web/frontend/style.css |
