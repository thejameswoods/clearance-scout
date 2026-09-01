from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from web.backend.routes import admin, deals, logs, products, scan, settings, vnc

app = FastAPI(title="Clearance Scout")

# Permissive by design, not oversight: this app already has no auth and is
# documented as LAN-only (see README's "Access control" section) -- a
# browser extension's popup (chrome-extension:// origin) needs this to
# read responses at all, and nothing here is more exposed by allowing it
# than the no-auth API already is.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.include_router(admin.router)
app.include_router(deals.router)
app.include_router(logs.router)
app.include_router(products.router)
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


@app.middleware("http")
async def no_cache_static(request, call_next):
    # StaticFiles only sends Last-Modified/ETag by default, which leaves
    # browsers free to serve a stale copy from heuristic caching with no
    # request at all -- confirmed live 2026-08-31: an old cached index.html
    # (looking for a since-removed element) paired with a freshly-served
    # app.js broke the page silently on every redeploy until a hard
    # refresh. This forces revalidation on every load for the frontend
    # (not /api/* or /vnc/*, which already set their own semantics) --
    # cheap for a small, low-traffic LAN dashboard.
    response = await call_next(request)
    if not request.url.path.startswith(("/api/", "/vnc/")):
        response.headers["Cache-Control"] = "no-cache"
    return response
