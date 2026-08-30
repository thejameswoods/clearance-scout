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
- A way to reach the scanner's noVNC port (6901) without exposing it
  publicly — an SSH tunnel is the simplest: `ssh -L 6901:localhost:6901 you@host`.
- Optionally, a Telegram bot token from [@BotFather](https://t.me/BotFather)
  if you want alerts.

## Steps

1. `git clone` this repo, `cd` into it.
2. `cp .env.example .env` and fill in `POSTGRES_PASSWORD`, `ZIP_CODE`, and
   (if using Telegram) `TELEGRAM_BOT_TOKEN`.
3. Bring up the database and scanner first, so there's something to log
   into:
   ```
   docker compose up -d db scanner
   ```
4. Tunnel to noVNC and log into the retailer's site by hand (handle any
   CAPTCHA/2FA yourself — this is a one-time step per adapter):
   ```
   ssh -L 6901:localhost:6901 you@host
   ```
   then open `http://localhost:6901` in a browser and log in inside the
   Chromium window you see there.
5. Bring up the rest:
   ```
   docker compose up -d web
   # only if you want Telegram alerts:
   docker compose --profile telegram up -d bot
   ```
6. Visit `http://<host>:8000` (or whatever `WEB_PORT` you set) for the
   dashboard.

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
