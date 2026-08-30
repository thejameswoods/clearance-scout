"""Embeds the scanner's noVNC session directly in the dashboard — no SSH
tunnel, no separate noVNC client/login. Proxies both the static noVNC web
assets (plain HTTP) and the actual VNC-over-WebSocket stream through to
`scanner:6901`, so the browser only ever talks to the dashboard's own
origin.

No auth on this route today — matches the app's current no-auth state
overall (internal-only deployment for now, see README). Once the dashboard
gets real auth, this is the highest-value place to make sure it's actually
enforced: unlike the deal feed, this hands out live control of an
authenticated retailer session to whoever can reach it.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import websockets
from fastapi import APIRouter, Response, WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

router = APIRouter(prefix="/vnc", tags=["vnc"])

SCANNER_NOVNC_HOST = os.environ.get("SCANNER_NOVNC_HOST", "scanner:6901")


@router.get("/{path:path}")
async def proxy_static(path: str):
    """Static noVNC client files (vnc.html, app/*.js, ...) served by
    websockify's built-in web server on the scanner container."""
    url = f"http://{SCANNER_NOVNC_HOST}/{path}"
    try:
        async with httpx.AsyncClient() as client:
            upstream = await client.get(url, timeout=10)
    except httpx.HTTPError as exc:
        return Response(
            content=f"Can't reach the scanner's noVNC endpoint ({exc}). Is the scanner container running?",
            status_code=502,
            media_type="text/plain",
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


@router.websocket("/{path:path}")
async def proxy_websocket(websocket: WebSocket, path: str):
    """The actual VNC stream. websockify doesn't care about the exact path
    here — any WebSocket upgrade gets proxied to the VNC server it's
    fronting — so this just needs to stay consistent with the path noVNC's
    client JS is told to connect to (see web/frontend's Browser tab)."""
    # noVNC's client always requests the "binary" subprotocol
    # (rfb.js: `this._sock.open(this._url, ['binary'])`). Per RFC 6455
    # 4.2.2, if a client offers subprotocols and the server's handshake
    # response doesn't confirm one, the BROWSER's own WebSocket
    # implementation is required to fail the connection -- not app JS, the
    # browser itself. `curl` doesn't enforce this, so a raw handshake test
    # looked fine while every real browser correctly refused to connect
    # (code 1006). Echo back whatever the client offered.
    offered = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol=offered[0] if offered else None)
    upstream_url = f"ws://{SCANNER_NOVNC_HOST}/{path}"

    try:
        upstream = await websockets.connect(upstream_url)
    except (OSError, InvalidHandshake):
        await websocket.close(code=1011, reason="Can't reach the scanner's noVNC endpoint")
        return

    # Not `async with upstream:` -- the object `websockets.connect()` hands
    # back once already awaited (websockets.legacy.client.WebSocketClientProtocol
    # in 13.1) doesn't implement the async context manager protocol itself;
    # only `websockets.connect(...)` used directly in an `async with` does.
    # Confirmed live: that raised TypeError on every connection, which is
    # why the Browser tab wasn't connecting at all.
    try:
        async def browser_to_upstream():
            try:
                while True:
                    data = await websocket.receive_bytes()
                    await upstream.send(data)
            except (WebSocketDisconnect, ConnectionClosed):
                pass

        async def upstream_to_browser():
            try:
                async for message in upstream:
                    await websocket.send_bytes(message)
            except ConnectionClosed:
                pass

        await asyncio.gather(browser_to_upstream(), upstream_to_browser())
    finally:
        await upstream.close()
