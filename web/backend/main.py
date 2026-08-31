from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from web.backend.routes import deals, scan, settings, vnc

app = FastAPI(title="Clearance Scout")

# Permissive by design, not oversight: this app already has no auth and is
# documented as LAN-only (see README's "Access control" section) -- a
# browser extension's popup (chrome-extension:// origin) needs this to
# read responses at all, and nothing here is more exposed by allowing it
# than the no-auth API already is.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.include_router(deals.router)
app.include_router(scan.router)
app.include_router(settings.router)
app.include_router(vnc.router)

_build_time_path = Path(__file__).resolve().parent.parent / "BUILD_TIME"
BUILD_TIME = _build_time_path.read_text().strip() if _build_time_path.exists() else "dev (not built in a container)"


@app.get("/api/health")
def health():
    return {"ok": True, "build_time": BUILD_TIME}


# Static frontend (plain HTML/CSS/JS, no build step) is copied into the
# image at web/frontend/ — see web/Dockerfile. Mounted last so it doesn't
# shadow the /api routes above.
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
