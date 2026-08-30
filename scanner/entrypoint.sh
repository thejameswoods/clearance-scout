#!/usr/bin/env bash
set -euo pipefail

Xvfb "$DISPLAY" -screen 0 1280x800x24 &
sleep 1

x11vnc -display "$DISPLAY" -forever -shared -nopw -quiet -rfbport 5900 &
websockify --web=/usr/share/novnc 6901 localhost:5900 &

echo "noVNC available on :6901 (tunnel this over SSH — never expose it directly, it holds a live authenticated session)"

exec python -m scanner.main
