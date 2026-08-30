#!/usr/bin/env bash
set -euo pipefail

# A fresh container start can never have a real conflicting Chromium
# process (it's a new PID namespace) -- but an unclean previous exit
# (crash, `docker restart`, OOM-kill) can leave Chromium's SingletonLock/
# SingletonSocket/SingletonCookie behind in the persistent profile volume,
# which makes Chromium refuse to launch, thinking another instance owns
# the profile. Confirmed reproducing this after a crash loop -- clean it
# unconditionally before every start.
rm -f "${PLAYWRIGHT_PROFILE_DIR:-/data/browser-profile}"/Singleton{Lock,Socket,Cookie}

Xvfb "$DISPLAY" -screen 0 1280x800x24 &
sleep 1

x11vnc -display "$DISPLAY" -forever -shared -nopw -quiet -rfbport 5900 &
websockify --web=/usr/share/novnc 6901 localhost:5900 &

echo "noVNC available on :6901 (tunnel this over SSH — never expose it directly, it holds a live authenticated session)"

exec python -m scanner.main
