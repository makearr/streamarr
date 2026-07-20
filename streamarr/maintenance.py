import logging
import os
import re
import subprocess
import sys
import threading
import time

import yt_dlp
from prometheus_client import Counter, Gauge

from . import config, db, subscriptions
from .runtime import set_status, clear_status

log = logging.getLogger("streamarr.maintenance")

M_SEARCHES = Counter("streamarr_searches_total", "Searches", ["indexer"])
M_GRABS = Counter("streamarr_grabs_total", "Grabs", ["indexer"])
M_DL_OK = Counter("streamarr_downloads_completed_total", "Completed downloads", ["indexer"])
M_DL_FAIL = Counter("streamarr_downloads_failed_total", "Failed downloads", ["indexer"])
M_QUEUE = Gauge("streamarr_queue_size", "Active queue size")


def metrics_refresh():
    M_QUEUE.set(len(db.jobs_active()))


def record_metric(indexer_id, event):
    m = {"search": M_SEARCHES, "grab": M_GRABS,
         "download_ok": M_DL_OK, "download_fail": M_DL_FAIL}.get(event)
    if m:
        m.labels(indexer=indexer_id or "manual").inc()


def ytdlp_version():
    return yt_dlp.version.__version__


def _downloading():
    return any(j["status"] == "Downloading" for j in db.jobs_active())


def restart_app(reason):
    """Exit the process; docker's restart policy brings a fresh instance up."""
    log.warning("Restarting Streamarr: %s", reason)
    time.sleep(1)
    os._exit(3)


def restart_when_idle(reason):
    def _wait():
        while _downloading():
            time.sleep(10)
        restart_app(reason)
    threading.Thread(target=_wait, daemon=True, name="restart-wait").start()


def update_ytdlp():
    """Returns (ok, changed, detail)."""
    set_status("Updating yt-dlp")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--user", "--break-system-packages", "yt-dlp"],
            capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            log.error("yt-dlp update failed: %s", (r.stderr or r.stdout)[-500:])
            return False, False, (r.stderr or r.stdout)[-500:]
        m = re.search(r"Successfully installed .*yt-dlp-([\w.]+)", r.stdout)
        if m:
            log.info("yt-dlp updated to %s", m.group(1))
            return True, True, f"updated to {m.group(1)}"
        return True, False, "already up to date"
    except Exception as exc:
        log.error("yt-dlp update error: %s", exc)
        return False, False, str(exc)
    finally:
        clear_status()


def _auto_update():
    """Update; if a new version landed and restarts are allowed, restart once downloads settle."""
    cfg = config.get()["ytdlp"]
    if _downloading():
        log.info("yt-dlp update postponed — downloads active")
        return False
    ok, changed, detail = update_ytdlp()
    if ok and changed and cfg.get("restart_after_update", True):
        restart_when_idle(f"loading new yt-dlp ({detail})")
    return ok


def startup_update():
    if config.get()["ytdlp"]["auto_update"]:
        threading.Thread(target=_auto_update, daemon=True, name="ytdlp-boot-update").start()


def _loop():
    last_update = time.time()  # boot update is handled by startup_update()
    while True:
        cfg = config.get()
        try:
            db.cache_prune(cfg["cache"]["retention_days"])
        except Exception as exc:
            log.warning("Cache prune failed: %s", exc)
        if cfg["ytdlp"]["auto_update"]:
            interval = cfg["ytdlp"]["update_interval_hours"] * 3600
            if time.time() - last_update > interval and _auto_update():
                last_update = time.time()
        if cfg["subscriptions"].get("enabled"):
            try:
                subscriptions.process()  # per-subscription intervals are enforced inside
            except Exception as exc:
                log.warning("Subscription run failed: %s", exc)
        metrics_refresh()
        time.sleep(300)


def start():
    threading.Thread(target=_loop, daemon=True, name="maintenance").start()
