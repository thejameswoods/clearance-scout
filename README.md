# Clearance Scout

A self-hosted service that watches local retail stores for clearance
("yellow tag") and penny-priced markdowns, using a real logged-in browser
so it looks like an ordinary shopper — not a scraping farm. Instead of a
browser-extension popup you have to remember to check, you get a dashboard
that's always up to date and, optionally, Telegram alerts the moment a new
deal shows up.

Built for Home Depot first; the retailer-specific logic is isolated behind
a small adapter interface so other local retailers can be added without
touching the scanner engine, database schema, dashboard, or bot.

**This is not a hosted service, and it never will be one without solving a
much harder problem (see [Why not a hosted website?](#why-not-a-hosted-website)
below). Each deployment is one person, one account, one home IP.**

## How it works

Four containers, one Docker Compose stack:

- **`scanner`** — a real, persistent Chrome instance ([Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python),
  not vanilla Playwright — see [`docs/architecture.md`](docs/architecture.md#patchright-not-vanilla-playwright))
  with its own logged-in session. Runs a three-phase scan (map departments →
  collect product IDs → check prices) on a schedule, pacing requests and
  backing off hard on a 403 instead of hammering the site.
- **`db`** — Postgres. Every price check is stored, so you get real
  cross-store and cross-time history, not just "what's on sale right now."
- **`web`** — a dashboard: filterable deal feed, price history per item,
  mark-bought/dismiss, scan status, and a **Browser** tab that embeds the
  scanner's live session directly — that's also where the one-time retailer
  login happens, no separate noVNC client or SSH tunnel needed.
- **`bot`** *(optional)* — a standalone Telegram bot for alerts and remote
  scan control.

See [`docs/deploy-generic.md`](docs/deploy-generic.md) to run it anywhere
Docker runs, or [`docs/deploy-proxmox-lxc.md`](docs/deploy-proxmox-lxc.md)
for Proxmox specifically. For the reasoning behind these design choices
(no VPN, self-hosted-per-instance, the adapter pattern, etc.), see
[`docs/architecture.md`](docs/architecture.md).

## Quickstart

```
git clone <this repo>
cd clearance-scout
cp .env.example .env   # fill in POSTGRES_PASSWORD, ZIP_CODE, etc.
docker compose up -d db scanner web
```

Open the dashboard (`http://<host>:8000`) and click the **Browser** tab —
that's the scanner's live Chromium session, embedded directly, no separate
noVNC client or SSH tunnel. Log into the retailer's site there by hand
(one-time — this is deliberately a human step, not automated, since it may
involve a CAPTCHA or 2FA). `NOVNC_PORT` in `.env` still exists as a direct
fallback if you'd rather reach it over `ssh -L 6901:localhost:6901`.

Then, optionally, bring up the bot:

```
docker compose --profile telegram up -d bot   # only if TELEGRAM_BOT_TOKEN is set
```

Full install walkthrough: [`docs/deploy-generic.md`](docs/deploy-generic.md).
Once it's up: [`QUICKSTART.md`](QUICKSTART.md) — the dashboard tour, config
reference, and finishing a retailer's real API endpoints.

## Access control (currently: none)

There's no login on the dashboard yet — it's built for a trusted, internal
network (your home LAN), not the open internet. That matters more than it
sounds: the **Browser** tab hands out live control of an authenticated
retailer session to whoever can reach the dashboard's URL, not just a view
of your deal history. Don't publish `WEB_PORT` beyond your LAN (no port
forwarding, no reverse-proxying it publicly) until real auth is added —
track/contribute to that here if it matters for your deployment.

## Narrowing what gets scanned

Most people care about a handful of departments, not the whole store. Two
`.env` settings narrow the scan itself (not just the dashboard filters) —
fewer departments listed, fewer products price-checked, which also means
less traffic against the retailer's site:

```
RADIUS_MILES=25
WATCHED_DEPARTMENTS=Electrical
WATCH_KEYWORDS=wire
```

That combination — any clearance electrical wire at any store within 25
miles — is the flagship example this was built for. Both settings are
comma-separated, case-insensitive substring matches; leave either blank to
scan everything in that dimension. `RADIUS_MILES` controls how many nearby
stores get scanned, not just the single nearest one.

## Why not a VPN?

It might seem safer to route the scanner's traffic through a VPN. For this
specific use case it's likely the opposite: the scan authenticates as your
real account, at your real local store, from (normally) your real home
address. A residential IP matching that account looks like an ordinary
shopper. A VPN or datacenter-looking IP on an account that's otherwise
behaving like a real person is a mismatch that's more likely to trigger
extra scrutiny, not less. Route it however you like, but that's the
reasoning behind the default.

## Why not a hosted website?

A centralized, multi-tenant version of this would reintroduce exactly the
problem a self-hosted, single-account, single-IP tool avoids: many users'
traffic funneling through one service looks nothing like ordinary shopping
and gets detected/blocked fast. That's a genuinely hard problem (rotating
real residential identities per user, at scale, without it being sketchy in
its own right) and not one this project tries to solve. Self-hosting your
own instance sidesteps it entirely — that's a feature of the architecture,
not a limitation to work around later.

## Adding a retailer

See [`adapters/README.md`](adapters/README.md) for the contract. In short:
implement `RetailerAdapter`, register it in `adapters/registry.py`, done —
no other code needs to change.

## Disclaimer

Automating interaction with a retailer's site this way likely violates its
Terms of Service. This project is provided as-is (see [`LICENSE`](LICENSE),
MIT — no warranty); running it, and any consequences of running it against
a given retailer's account/IP, is on you. It's built to be conservative
about pacing and to back off hard on any sign of trouble, but "conservative"
isn't "risk-free."

## Development

```
pip install -r requirements-dev.txt -r scanner/requirements.txt -r web/requirements.txt -r bot/requirements.txt
ruff check .
pytest   # signal-parsing and rate-limit tests run with no dependencies;
         # the orchestrator/filtering/multi-store tests need a Postgres
         # reachable at TEST_DATABASE_URL — see tests/conftest.py
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs lint + the
full test suite against a real Postgres service container on every push and
PR, then builds all three service images to catch Dockerfile breakage.
New behavior should land test-first: add a failing test against the fake
adapter in `tests/fakes.py` (or extend it), watch it fail for the right
reason, then implement.
