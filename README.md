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

- **`scanner`** — a real, persistent Chromium instance (Playwright) with
  its own logged-in session. Runs a three-phase scan (map departments →
  collect product IDs → check prices) on a schedule, pacing requests and
  backing off hard on a 403 instead of hammering the site.
- **`db`** — Postgres. Every price check is stored, so you get real
  cross-store and cross-time history, not just "what's on sale right now."
- **`web`** — a dashboard: filterable deal feed, price history per item,
  mark-bought/dismiss, scan status.
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
docker compose up -d db scanner
```

Tunnel to the scanner's noVNC port and log into the retailer's site by hand
(one-time — this is deliberately a human step, not automated, since it may
involve a CAPTCHA or 2FA):

```
ssh -L 6901:localhost:6901 you@host
# open http://localhost:6901 and log in
```

Then bring up the dashboard (and, optionally, the bot):

```
docker compose up -d web
docker compose --profile telegram up -d bot   # only if TELEGRAM_BOT_TOKEN is set
```

Full walkthrough: [`docs/deploy-generic.md`](docs/deploy-generic.md).

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
pytest   # signal-parsing and rate-limit tests run with no dependencies;
         # the orchestrator end-to-end test needs a Postgres reachable at
         # TEST_DATABASE_URL — see tests/conftest.py
```
