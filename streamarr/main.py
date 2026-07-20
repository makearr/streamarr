import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

import threading

from . import VERSION, arr, config, db, downloader, maintenance
from .api import newznab, sabnzbd, ui
from .runtime import setup_logging

log = logging.getLogger("streamarr")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app):
    cfg = config.load()
    setup_logging(cfg["streamarr"]["log_level"])
    db.stat_hook = maintenance.record_metric
    downloader.start()
    maintenance.start()
    threading.Thread(target=arr.sync_all, daemon=True, name="arr-sync").start()
    maintenance.startup_update()
    log.info("Streamarr %s started (config: %s)", VERSION, config.CONFIG_FILE)
    yield


app = FastAPI(title="Streamarr", version=VERSION, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


app.include_router(newznab.router)
app.include_router(sabnzbd.router)
app.include_router(ui.router)


@app.get("/ping")
def ping():
    return PlainTextResponse("pong")


@app.get("/metrics")
def metrics():
    maintenance.metrics_refresh()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


_INDEX = None


@app.get("/{path:path}")
def spa(path: str, request: Request):
    # single-page UI; auth is enforced by the JSON API, not by static delivery.
    # no-store + versioned asset URLs so browsers never serve a stale app shell.
    global _INDEX
    if _INDEX is None:
        with open(os.path.join(STATIC_DIR, "index.html")) as f:
            _INDEX = f.read().replace("{{V}}", VERSION)
    return Response(_INDEX, media_type="text/html",
                    headers={"Cache-Control": "no-store"})
