# Deploying in a Proxmox LXC

This is one specific way to run the generic setup in
[`deploy-generic.md`](deploy-generic.md) — nothing in the app itself is
Proxmox-specific. Skip this doc entirely if you're not on Proxmox.

## Provisioning the container

Docker-in-LXC needs an **unprivileged container with nesting enabled**:

```
pct create <ctid> local:vztmpl/debian-12-standard_*.tar.zst \
  --hostname clearance-scout \
  --unprivileged 1 --features nesting=1 \
  --cores 2 --memory 4096 --swap 512 \
  --rootfs local-lvm:20 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp
```

Notes:

- `tag=<vlan>` on `--net0` if your network uses tagged VLANs for
  application containers — put this on whatever VLAN your other always-on
  app containers use. It does **not** need to be on a VPN/anonymizing
  network — see the main [README](../README.md#why-not-a-vpn) for why a
  plain residential-looking egress is actually the safer choice here.
- 2 vCPU / 4 GiB RAM / 20 GiB disk is a reasonable starting size — the
  headed Chromium-under-Xvfb in the `scanner` container is the heaviest
  single piece; bump RAM if scans or noVNC feel sluggish.
- Start the container, then inside it: install Docker Engine + the Compose
  plugin (standard `get.docker.com` convenience script or your distro's
  packages both work), then follow [`deploy-generic.md`](deploy-generic.md)
  from step 2 onward, cloning the repo to e.g. `/opt/clearance-scout`.

## The one-time login

Reach the dashboard on your LAN (`http://<lxc-ip>:8000` or your internal
DNS name) and use its **Browser** tab — no separate noVNC step needed on
Proxmox specifically, that's the whole point of the embedded proxy. The
direct noVNC port (6901) is still there as a fallback for debugging; don't
publish it through any reverse proxy or firewall rule either way — it
drives a live, authenticated browser session with no auth of its own:

```
ssh -L 6901:localhost:6901 root@<lxc-ip>
```

## Reverse proxy / DNS

If you run something like Nginx Proxy Manager and internal DNS (AdGuard,
Pi-hole, etc.) already, add a proxy host pointing at
`<lxc-ip-or-hostname>:8000` and put an access list or basic auth in front of
it — the app itself has no login yet (see the README), and the dashboard
now embeds live control of an authenticated retailer session (the Browser
tab), not just your shopping activity.

## Backups

Back up the `db-data` and `browser-profile` Docker volumes (Proxmox Backup
Server, or any volume-aware backup tool) — `browser-profile` in particular
holds your live login and isn't worth re-doing the manual noVNC step for.
