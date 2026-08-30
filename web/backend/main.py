from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.backend.routes import deals, scan, settings, vnc

app = FastAPI(title="Clearance Scout")

app.include_router(deals.router)
app.include_router(scan.router)
app.include_router(settings.router)
app.include_router(vnc.router)


@app.get("/api/health")
def health():
    return {"ok": True}


# Static frontend (plain HTML/CSS/JS, no build step) is copied into the
# image at web/frontend/ — see web/Dockerfile. Mounted last so it doesn't
# shadow the /api routes above.
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
