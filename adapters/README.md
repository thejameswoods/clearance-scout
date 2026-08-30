# Writing a retailer adapter

An adapter is a class implementing `RetailerAdapter` from [`base.py`](base.py),
registered in [`registry.py`](registry.py). It's the only place that knows
what a given retailer's pages/API responses actually look like — the
scanner's `orchestrator.py`, the Postgres schema, the web dashboard, and the
Telegram bot only ever see the shared dataclasses (`Department`, `ProductRef`,
`StoreInfo`, `PriceObservation`, ...).

## The contract

See the docstrings in [`base.py`](base.py) for the full method list. In short,
a scan finds every store in radius, then runs three phases per store, and
your adapter drives each one:

0. **`find_stores`** + **`select_store`** — every store within radius,
   scanned in turn (see below).
1. **`discover_departments`** — map the site's category structure.
2. **`list_products`** — enumerate products per department. Don't dedupe
   against what's already in the DB yourself; the orchestrator owns that
   caching decision.
3. **`check_price`** — look up one product's current price/clearance/penny
   state at the selected store.

Plus:

- **`authenticate`** — is the persistent browser session still logged in?
  Raise `NeedsLogin` if not. Never try to log back in silently — that's a
  human-with-noVNC task, on purpose (CAPTCHAs, 2FA, ToS risk).
- **`find_stores`** — resolve every store within a radius of a ZIP code,
  not just the nearest one, so a watch can span multiple nearby locations.
- **`select_store`** — make one of those stores active on the browser
  context for the department/product/price calls that follow. The
  orchestrator calls this once per store per scan.
- **`detect_clearance`** / **`detect_penny`** — pure functions over a raw
  API response / a `PriceObservation`. Keep these dependency-free so they're
  unit-testable against fixture JSON with no browser involved.
- **`rate_limit_policy`** — your own pacing numbers (min/max delay, backoff
  window on a 403/429). The generic engine in `scanner/ratelimit.py`
  enforces whatever you declare here; you don't write any pacing logic
  yourself.

## Ground rules

- **All requests go through the live browser context**, not a bare HTTP
  client with copied-out cookies. Use the context's own request API (e.g.
  `context.request`, same on Patchright as vanilla Playwright — see
  [`docs/architecture.md`](../docs/architecture.md#patchright-not-vanilla-playwright))
  or in-page `fetch()`. Bot detection on most large retail sites keys on
  more than cookies — TLS/JA3 fingerprint, browser fingerprint, timing, and
  the CDP automation connection itself — and a bare `requests.Session()`
  with stolen cookies tends to get flagged even when the cookies are
  genuinely valid.
- **Never store credentials or session tokens outside the browser profile
  volume.** The DB's `credential_session` table is metadata only
  (`valid` / `expired` / `needs_login`) — no adapter should ever write a
  cookie or token into Postgres, a log line, or an env var.
- **Respect the site.** Pace requests, back off hard on 403/429, and don't
  try to defeat CAPTCHAs programmatically — surface `NeedsLogin` and let a
  human handle it over noVNC. This project automates something that likely
  violates the target site's Terms of Service; the least you can do is not
  hammer it.

## Testing your adapter without hitting the real site

Write fixture-based unit tests for `detect_clearance` / `detect_penny`
against captured (and hand-scrubbed, no real account data) raw responses.
For an end-to-end check that your adapter actually satisfies the contract,
see `tests/test_fake_adapter.py` — it runs a trivial in-memory
`FakeRetailerAdapter` through the real `orchestrator.py` and asserts nothing
retailer-specific leaks into the engine. Your adapter should pass the same
kind of test.
