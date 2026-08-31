# Architecture and design rationale

This is the "why," for anyone (including future-me) wondering why a design
choice was made. See [`README.md`](../README.md) for the "what" and
[`adapters/README.md`](../adapters/README.md) for how to extend it.

## Origin

This project replaces a Chrome extension ([HDScanner](https://github.com/apedonkey/hdscanner))
used to find in-store clearance and penny-priced items at Home Depot. The
extension works, but its popup UI doesn't hold up under heavy, sustained
use (e.g. shopping continuously for a large renovation) — it's easy to lose
track of what you've already seen, and it only runs while a browser tab is
open. This project turns the same idea into an always-on service with
persistent history and a real dashboard.

HDScanner has no license, so nothing here is copied from it — the design
below reimplements its documented *behavior* (see the "How the scanning
actually works" section) as new code.

## Design decisions

### Self-hosted per instance, not a hosted service

Each deployment is one person's own account, on their own home IP,
watching their own local stores. This is deliberate, not a limitation to
fix later:

- Home Depot's own signal that "this is a normal shopper" is a real
  logged-in session behaving like a person, from a residential address.
  A single-account, single-IP deployment matches that by construction.
- A centralized, multi-tenant hosted version would funnel many users'
  traffic through one service, which looks nothing like ordinary shopping
  and would get detected/blocked fast — reintroducing exactly the problem
  this architecture avoids. Solving *that* (many real-looking identities,
  at scale, without itself being sketchy) is a distinct, much harder
  project and explicitly out of scope here.

### Direct residential egress, no VPN

It's tempting to route scraping traffic through a VPN for anonymity, and
that's a reasonable default for a lot of scraping use cases. It's likely
counterproductive here specifically: the scan authenticates as a real
account at a real local store. A VPN or datacenter-looking IP on an
account that's otherwise behaving like an ordinary shopper is a mismatch
more likely to draw scrutiny, not less. Run it however you like, but the
default assumes plain home internet.

### Narrow by default, at the request level

`WATCHED_DEPARTMENTS` and `WATCH_KEYWORDS` filter *what gets requested*,
not just what the dashboard displays. The orchestrator resolves every store
within `RADIUS_MILES` (`find_stores`), then for each store only lists
products for departments whose name matches a watched substring, and only
price-checks products whose name matches a watched keyword. An unwatched
department's product list is never even requested. This isn't just a UX
convenience — a smaller, targeted request footprint is also lower
detection risk than crawling an entire store's catalog to filter it
client-side afterward. Leaving both settings blank scans everything, which
is the original (v1) behavior.

An explicit single-department manual scan (dashboard button, or the bot's
`/scan <department>`) overrides the watch list rather than being
constrained by it — checking something outside your usual watch list on
demand shouldn't require reconfiguring it first.

### Retailer-agnostic from the start

Home Depot is the only implemented adapter, but the goal was always to add
other local retailers' clearance pages later. Rather than hard-code Home
Depot logic throughout and refactor later, the schema, orchestrator,
dashboard, and bot only ever speak in retailer-agnostic terms (see
`adapters/base.py`); a retailer's specifics live entirely inside its own
`adapters/<slug>/` package. Adding a second retailer should be additive —
a new package plus a registry entry — not a rewrite.

### Requests go through the live browser session, not a bare HTTP client

HDScanner's own description is that it calls Home Depot's product/pricing
API "from within your browser tab" rather than treating it as a plain REST
API. That phrasing implies detection likely isn't cookie-only — TLS/browser
fingerprint and timing probably matter too. So every adapter call routes
through the live browser context (its own request API, which shares
cookies/headers/fingerprint with the real session), not a copied-out
cookie jar in a separate HTTP client.

### Patchright, not vanilla Playwright

Confirmed live, not theoretical: Home Depot's real login API (`POST
/customer/auth/v1/twostep/init`) returned 403 on the very first login
attempt, before any meaningful request volume existed — that's
fingerprint-based bot detection (Home Depot uses PerimeterX/HUMAN, visible
from a separate CSP log referencing `px-cloud.net`), not rate-based
blocking. Manually stripping the obvious tells (`navigator.webdriver` via
launch args) wasn't enough, because the deeper leak is the CDP connection
itself (`Runtime.enable`) that Playwright needs to drive the browser at
all — not something JS-observable, so no amount of launch-arg tweaking
touches it.

[Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) is
a maintained fork of Playwright's Python driver (not a plugin layered on
top) that patches exactly this class of leak, and is a drop-in replacement
for the `playwright` package (same API, `from patchright.sync_api import
sync_playwright`). Used per its own documented "best practice" config:
real Google Chrome (`channel="chrome"`, fetched via `patchright install
chrome`, not the open-source Chromium build), `no_viewport=True`, and no
custom user agent or headers.

Worth naming honestly: this is an unofficial, community-maintained fork of
core browser-automation internals, not something Microsoft/Anthropic
vets — a real third-party trust call, not a purely technical one. It's the
standard tool in this space for exactly this problem, and every adapter
call still routes through whatever the orchestrator hands it (`Any`-typed
`browser_ctx` in `adapters/base.py`), so this swap touched exactly three
files (`scanner/main.py`, `scanner/Dockerfile`,
`scanner/requirements.txt`) — no adapter code needed to know or care which
library produced the browser context.

### A real content script as the API-call execution context (tried, ruled out)

Even with Patchright's CDP-level patches, a real request confirmed
correct (see "Replace placeholder Home Depot endpoints" below) still got
a generic degraded response from Home Depot's actual API — tried both
`context.request` and a genuine in-page `fetch()` via `page.evaluate()`,
with both short and long (20s) post-load dwell times, all identical
results. A real, installed browser extension is a different execution
context than either of those and was the one thing not yet tried, so
`browser-extension/home_depot/` was built specifically to be that
context: its content script (`content.js`) did the actual `fetch()`
call, bridged from Python via `page.evaluate()` posting a
`window.postMessage` the content script listened for.

**Outcome: ruled out, not confirmed.** Under vanilla Playwright (needed
because Patchright silently strips `--load-extension` and
`--disable-extensions-except` as part of its own anti-detection arg
sanitization — confirmed via isolated diagnostic, no error, just an empty
`chrome://extensions` page and `service_workers: []`), a genuinely-loaded
content script got the *identical* generic degraded response as the
other two transports. That rules out execution context as the variable
entirely — three different origins for the same `fetch()` call, three
identical results. `adapters/home_depot/api_client.py` has since reverted
to a direct in-page `fetch()` via `page.evaluate()` (the simplest of the
three, and Patchright-compatible), since the extension bridge was adding
a dependency on something Patchright can't actually load without buying
anything in return. The `browser-extension/home_depot/` code is left in
the repo (unused by the Home Depot adapter) rather than deleted — it's
still a reasonable pattern for a *future* adapter running under vanilla
Playwright specifically, just not this one.

Whatever is producing the generic response, it isn't which JS context the
`fetch()` originates from. Leading unconfirmed hypothesis as of
2026-08-30: cumulative same-IP request volume across many diagnostic
browser profiles in one day tripped an Akamai velocity/reputation signal
— if so, the fix is a real cooldown period and a single clean retest, not
another transport variation.

### One long-lived, persistent, human-authenticated browser identity

Login (including any CAPTCHA/2FA) is a one-time, human-driven step over
noVNC — never automated. The browser profile (cookies, session, local
storage) persists in a Docker volume across restarts so that one login
lasts. If the session goes stale, the adapter raises `NeedsLogin` rather
than silently retrying or attempting to defeat a challenge — that's a
human problem, on purpose.

That login step is embedded directly in the dashboard (the **Browser**
tab), rather than requiring a separate noVNC client and an SSH tunnel.
`web/backend/routes/vnc.py` proxies both the static noVNC assets and the
actual VNC-over-WebSocket stream from `scanner:6901` through the
dashboard's own origin — same idea as the existing scan-status proxy
(`routes/scan.py`), just extended to a WebSocket. This is a genuine
tradeoff, not a free win: it means the dashboard now hands out live,
unauthenticated control of an authenticated retailer session to anyone who
can reach it, and the dashboard has no login of its own yet (see the
README's "Access control" section). That's an accepted gap for a
trusted-LAN-only deployment, not an oversight — real auth is the natural
next addition once this needs to be reachable from anywhere less trusted.

### Login is per-adapter, not assumed

The contract above (`NeedsLogin`, the Browser-tab login flow) exists for
retailers that actually need it — it's not a universal requirement.
Confirmed live for Home Depot specifically (2026-08-30): browsing
products/prices/clearance status by store doesn't require a logged-in
account, only account-specific things (order history, Pro pricing, saved
lists) do, none of which this scanner touches. `HomeDepotAdapter.
authenticate()` returns valid unconditionally and never navigates to
`/myaccount/` during a scan — deliberately, since that's the account/auth
surface and there's no reason to poke it when nothing downstream needs it.

This was also a practical call, not just a correctness one: Home Depot's
real login API 403's even with Patchright's CDP-level fingerprint
patches applied (see above) — solving that further was tabled as a
harder problem than this project needs to solve for its actual goal. A
future adapter for a retailer that genuinely requires login for pricing
should implement a real `authenticate()` check rather than assuming this
pattern (login not required) generalizes.

### Product-ID listing is cached; price checks never are

Phase 2 (which products exist in a department) and phase 3 (a product's
current price/clearance) have very different freshness requirements, but
every scan re-ran both from scratch until 2026-08-31 -- confirmed live as
a real contributor to a multi-hour, 71-department scan that led to a
memory-exhaustion incident (`GitHub issue #4`). Product IDs rarely change;
current price/clearance is the entire point of scanning and must always
be checked fresh. `department.products_last_listed_at` tracks when a
department was last listed; within `PRODUCT_LIST_CACHE_HOURS` (default
24), `run_scan` builds `ProductRef`s from the existing `product` table
(`common/db.py`'s `list_cached_products_for_department`) instead of
calling the adapter's `list_products()` again -- phase 3 still runs
against every one of those products, unaffected. This also means multiple
stores in one scan run only pay the phase-2 cost once, since the same
department gets discovered per store but the cache is now warm after the
first.

### Manual pacing/backoff engine, adapter-declared

Scraping is paced deliberately, and a 403 triggers an escalating backoff
rather than a retry loop. Each adapter *declares* its own pacing numbers
(`RateLimitPolicy`); a single generic engine (`scanner/ratelimit.py`)
*enforces* whatever's declared, so a new retailer adapter never needs to
reimplement pacing logic, just different numbers.

### Postgres as the source of truth, not browser storage

HDScanner keeps its history in the browser's IndexedDB, which is fine for
a single-tab tool but doesn't support a real always-on dashboard, remote
Telegram alerts, or a proper time series for cross-store/cross-time
comparison. Every price check is written to Postgres (`price_observation`),
with a derived `deal` table as the read model the dashboard and bot
actually query — so "is this deal still live" is computed once, on write,
not re-derived from raw rows on every page load.

## How the scanning actually works (confirmed from HDScanner's public
description, reimplemented here)

- Requires an authenticated `homedepot.com` browser session — real cookies,
  a real account — not anonymous/unauthenticated scraping.
- Store selection via ZIP code.
- Three phases per scan: (1) map departments, (2) collect and cache
  product IDs per department, (3) check each product's current
  price/clearance signal.
- "Clearance" detection keys on Home Depot's own advertised markdown
  signal (the "yellow tag").
- "Penny" detection separately looks for $0.01 items using price combined
  with a fulfillment-state signal (price alone isn't reliable — other
  states can also show $0.01).
- Aisle/bay location is captured when available, for in-store lookup.
- A 403 means back off — HDScanner's documented range is roughly 15
  minutes to several hours before retrying.

The exact Home Depot API endpoints and response shapes aren't public and
aren't hard-coded here from guesswork — see
[`adapters/home_depot/api_client.py`](../adapters/home_depot/api_client.py)
for the capture procedure that fills them in from real traffic.

## Component layout

Four containers (see [`docker-compose.yml`](../docker-compose.yml)):

- **`scanner`** — the persistent authenticated browser + the generic
  3-phase orchestrator + all registered retailer adapters. Exposes a small
  internal-only HTTP API (`/status`, `/trigger-scan`) for the other
  containers — never exposed publicly, since it fronts a live session.
- **`db`** — Postgres; schema in [`db/init/001_schema.sql`](../db/init/001_schema.sql).
- **`web`** — FastAPI backend + a plain HTML/CSS/JS dashboard (no build
  step, so no extra JS toolchain to keep patched for a project this size).
- **`bot`** *(optional, `--profile telegram`)* — a standalone Telegram bot
  polling for new deals and relaying scan control.

## Deliberately out of scope (for now)

- **Terraform/Ansible IaC** for the deployment itself — the compose file is
  `.env`-driven so it can be dropped into an IaC pipeline later without
  rework, but that's a follow-up, not part of the initial build.
- **A hosted, multi-tenant version** — see "Self-hosted per instance"
  above; a genuinely hard, separate problem.
- **Automated CAPTCHA/2FA handling** — always a human-over-noVNC step.
