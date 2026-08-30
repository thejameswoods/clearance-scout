# Deploying anywhere with Docker

This works on any Linux host with Docker Engine + the Compose plugin — a
VPS, a NAS, a spare machine, a Proxmox LXC (see
[`deploy-proxmox-lxc.md`](deploy-proxmox-lxc.md) for that specific case).

## Prerequisites

- Docker Engine 24+ and the `docker compose` plugin.
- A residential/home internet connection is strongly recommended for the
  `scanner` container's egress — see the [README](../README.md#why-not-a-vpn)
  for why routing this through a VPN or a datacenter IP is likely to work
  *against* you, not for you.
- **No login on the dashboard yet** (see [README](../README.md#access-control-currently-none))
  — only run this on a trusted LAN, don't publish `WEB_PORT` beyond it.
- Optionally, a Telegram bot token from [@BotFather](https://t.me/BotFather)
  if you want alerts.

## Steps

1. `git clone` this repo, `cd` into it.
2. `cp .env.example .env` and fill in `POSTGRES_PASSWORD`, `ZIP_CODE`, and
   (if using Telegram) `TELEGRAM_BOT_TOKEN`.
3. Bring up the database, scanner, and dashboard:
   ```
   docker compose up -d db scanner web
   # only if you want Telegram alerts:
   docker compose --profile telegram up -d bot
   ```
4. Visit `http://<host>:8000` (or whatever `WEB_PORT` you set), open the
   **Browser** tab, and log into the retailer's site by hand there (handle
   any CAPTCHA/2FA yourself — this is a one-time step per adapter). That
   tab is the scanner's live Chromium session, embedded in the dashboard —
   no separate noVNC client, no SSH tunnel, no second login.

   If you'd rather not go through the dashboard for this (debugging, or
   the proxy isn't working for some reason), `NOVNC_PORT` in `.env` still
   exposes the same session directly on localhost:
   ```
   ssh -L 6901:localhost:6901 you@host
   # open http://localhost:6901
   ```

## Updating

```
git pull
docker compose build
docker compose up -d
```

The `browser-profile` and `db-data` volumes persist across rebuilds — you
won't need to log in again unless the retailer invalidates the session.

## Adding a retailer

See [`adapters/README.md`](../adapters/README.md).
