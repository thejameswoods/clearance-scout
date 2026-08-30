# Quickstart

For installation, see [`docs/deploy-generic.md`](docs/deploy-generic.md) or
[`docs/deploy-proxmox-lxc.md`](docs/deploy-proxmox-lxc.md). This is what to
do once the containers are up.

## 1. Log into the retailer (one-time)

1. Open the dashboard: `http://<host>:8000`.
2. Click the **Browser** tab. You'll see a live Chromium session running
   inside the `scanner` container.
3. Navigate to the retailer's site and log in there, same as any browser.
4. That's it. The session persists in the `browser-profile` Docker volume
   from then on — you shouldn't need this tab again unless the retailer
   logs you out.

## 2. The dashboard

| Tab | What it does |
|---|---|
| **Deals** | Live feed of clearance/penny finds — filter by department, store, discount %, search |
| **History** | Bought/dismissed/stale items, kept for reference |
| **Browser** | The live scanner session (login happens here) |
| **Settings** | Retailers, stores, Telegram binding status |

## 3. Telegram bot (if enabled)

Send it `/start` to bind your chat, then `/menu` for inline controls (scan
now, status, today's deals, pause/resume alerts), or `/scan [department]`
directly. Alerts arrive automatically as new deals are found.

## 4. Configuration reference (`.env`)

| Variable | Default | What it does |
|---|---|---|
| `ZIP_CODE` | — (required) | Center point for store discovery |
| `RADIUS_MILES` | `25` | How far from `ZIP_CODE` to look for stores — every store in range gets scanned |
| `WATCHED_DEPARTMENTS` | blank (all) | Comma-separated, case-insensitive substrings — only matching departments are scanned at all |
| `WATCH_KEYWORDS` | blank (all) | Further narrows by product name within watched departments |
| `SCAN_INTERVAL_MINUTES` | `240` | How often a scheduled scan runs |
| `RETAILERS` | `home_depot` | Comma-separated adapter slugs to run |

Edit `.env`, then `docker compose up -d scanner` (or `web`/`bot`, if those
settings changed instead) to apply.

## 5. Finishing a retailer adapter's real endpoints

Adapters ship with placeholder API endpoints (see each adapter's
`api_client.py`) — the retailer's internal pricing API isn't public, so
these have to come from real captured traffic, not guesswork. Until you do
this, the scanner runs safely (it retries on schedule, doesn't crash-loop,
never hammers the site) but won't find real deals. To finish it:

1. Complete the login step above.
2. Open DevTools → Network in that same Browser tab, browse a department
   and a product page.
3. Note the actual request URLs and JSON response shapes.
4. Fill those into the adapter's `api_client.py` (`*_ENDPOINTS`), and
   adjust the parsing in its `clearance.py` / `penny.py` /
   `departments.py` (or equivalent) to match.

See [`adapters/README.md`](adapters/README.md) for the full contract.

## 6. Handy commands

Run from the repo directory on the host:

```
docker compose ps                 # status of all services
docker compose logs scanner -f    # watch the scanner live
docker compose restart scanner    # after editing .env or adapter code
git pull && docker compose build  # pull + rebuild after a code update
```

## Troubleshooting

**Scanner container keeps restarting in a loop.** Check
`docker compose logs scanner` first. If it mentions Chromium's profile
being "in use by another process," an unclean previous exit left a stale
`SingletonLock` in the `browser-profile` volume — `scanner/entrypoint.sh`
clears this automatically on every start as of the current version, so
this shouldn't recur; if it does anyway, `docker compose stop scanner`,
then remove `SingletonLock`/`SingletonSocket`/`SingletonCookie` from the
volume by hand, then start it again.

**Scanner logs a clean "non-JSON response... this endpoint is still a
placeholder" error.** Expected until you've done step 5 above — it's not a
bug, it's the app telling you exactly what's left to do.
