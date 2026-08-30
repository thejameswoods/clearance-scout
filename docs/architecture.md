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
through the live Playwright browser context (its own request API, which
shares cookies/headers/fingerprint with the real session), not a copied-out
cookie jar in a separate HTTP client.

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
