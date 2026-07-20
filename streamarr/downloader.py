import logging
import os
import threading
import time
import uuid

import yt_dlp

from . import config, db, naming
from .providers import youtube
from .runtime import limiter, set_status, clear_status, log_connection_error

log = logging.getLogger("streamarr.downloader")

_wake = threading.Event()
_cancel = set()          # nzo_ids to abort
_cancel_lock = threading.Lock()
_progress = {}           # nzo_id -> {"done":…, "total":…, "speed":…}
_speed_hist = {}         # nzo_id -> [(ts, bytes_done), …] rolling ~75 s window
_last_speed_sample = 0.0


def _avg_speed(nzo_id):
    """Average download speed (bytes/s) over the last minute."""
    hist = _speed_hist.get(nzo_id) or []
    now = time.time()
    hist = [(t, b) for t, b in hist if now - t <= 75]
    _speed_hist[nzo_id] = hist
    if len(hist) < 2:
        return 0
    (t0, b0), (t1, b1) = hist[0], hist[-1]
    return (b1 - b0) / (t1 - t0) if t1 > t0 else 0


class _Abort(Exception):
    pass


_impersonate_target = "unset"  # cached probe result


def _apply_impersonation(opts, ytcfg):
    """Many sites (PornHub, others) now return HTTP 403 to the default client. yt-dlp can
    impersonate a real browser's TLS/HTTP fingerprint when curl_cffi is installed; enable it
    unless the user turned it off."""
    global _impersonate_target
    if ytcfg.get("impersonate") is False:
        return
    if _impersonate_target == "unset":
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({"quiet": True}) as probe:
                targets = probe._get_available_impersonate_targets() if hasattr(
                    probe, "_get_available_impersonate_targets") else []
            # entries are (ImpersonateTarget, handler_name); prefer a Chrome target
            chrome = next((t for t, _ in targets if getattr(t, "client", "") == "chrome"), None)
            _impersonate_target = chrome or (targets[0][0] if targets else None)
            if _impersonate_target:
                log.info("yt-dlp browser impersonation available (%s) — using it to avoid 403s",
                         _impersonate_target)
            else:
                log.warning("yt-dlp impersonation unavailable (curl_cffi not installed); some "
                            "sites like PornHub may return 403. Add curl_cffi to enable it.")
        except Exception as exc:
            log.debug("Impersonation probe failed: %s", exc)
            _impersonate_target = None
    if _impersonate_target:
        opts["impersonate"] = _impersonate_target


def _fs_safe(part):
    """No traversal, no separators, no hidden-file dots."""
    part = naming.clean(str(part or "")).replace("..", "").lstrip(". ")
    return part[:200]


def enqueue(name, url, category, indexer_id, provider, media, priority=0, outdir=None):
    name = _fs_safe(name) or "download"
    category = _fs_safe(category)
    nzo_id = f"SAR_{uuid.uuid4().hex[:12]}"
    pos = max([j["position"] for j in db.jobs_active()] or [0]) + 1
    db.job_save({
        "nzo_id": nzo_id, "name": name, "category": category or "", "indexer_id": indexer_id,
        "provider": provider, "url": url, "media": media, "status": "Queued",
        "priority": priority, "position": pos, "outdir": outdir,
    })
    db.stat(indexer_id or "manual", "grab", name)
    log.info("Queued '%s' (%s)", name, nzo_id)
    _wake.set()
    return nzo_id


def pause(nzo_id=None):
    if nzo_id is None:
        config.update("downloads", {"paused": True})
        log.info("Queue paused")
    else:
        j = db.job_get(nzo_id)
        if j and j["status"] in ("Queued", "Downloading"):
            if j["status"] == "Downloading":
                with _cancel_lock:
                    _cancel.add(nzo_id)  # abort + requeue as Paused
            j["status"] = "Paused"
            db.job_save(j)
    _wake.set()


def resume(nzo_id=None):
    if nzo_id is None:
        config.update("downloads", {"paused": False})
        log.info("Queue resumed")
    else:
        j = db.job_get(nzo_id)
        if j and j["status"] == "Paused":
            j["status"] = "Queued"
            db.job_save(j)
    _wake.set()


def delete(nzo_id):
    j = db.job_get(nzo_id)
    if not j:
        return False
    if j["status"] == "Downloading":
        with _cancel_lock:
            _cancel.add(nzo_id)
    db.job_delete(nzo_id)
    log.info("Removed '%s' from queue", j["name"])
    return True


def move(nzo_id, position):
    jobs = db.jobs_active()
    jobs = [j for j in jobs if j["nzo_id"] != nzo_id]
    target = db.job_get(nzo_id)
    if not target:
        return False
    jobs.insert(max(0, min(position, len(jobs))), target)
    for i, j in enumerate(jobs):
        j["position"] = i
        db.job_save(j)
    _wake.set()
    return True


def set_speed_limit(kbps):
    config.update("downloads", {"speed_limit_kbps": int(kbps)})
    log.info("Speed limit set to %s KB/s", kbps or "unlimited")


def queue_snapshot():
    jobs = db.jobs_active()
    for j in jobs:
        p = _progress.get(j["nzo_id"])
        if p and j["status"] == "Downloading":
            j["bytes_done"], j["bytes_total"] = p["done"], p["total"] or j["bytes_total"]
            avg = _avg_speed(j["nzo_id"]) or p.get("speed") or 0
            j["speed"] = avg
            left = max(0, (j["bytes_total"] or 0) - (j["bytes_done"] or 0))
            j["eta"] = int(left / avg) if avg > 0 and j["bytes_total"] else None
    return jobs


def _ydl_download(job):
    cfg = config.get()
    dl = cfg["downloads"]
    idx = next((i for i in cfg["indexers"] if i["id"] == job.get("indexer_id")), None)
    quality = config.quality_for(idx) if idx else cfg["quality"]
    media = job["media"] or "video"
    base = job.get("outdir") or os.path.join(dl["path"], job["category"] or "")
    outdir = os.path.join(base, job["name"])
    os.makedirs(outdir, exist_ok=True)

    def hook(d):
        with _cancel_lock:
            if job["nzo_id"] in _cancel:
                raise _Abort()
        if d["status"] == "downloading":
            done = d.get("downloaded_bytes") or 0
            _progress[job["nzo_id"]] = {
                "done": done,
                "total": d.get("total_bytes") or d.get("total_bytes_estimate") or 0,
                "speed": d.get("speed") or 0,
            }
            _speed_hist.setdefault(job["nzo_id"], []).append((time.time(), done))
            global _last_speed_sample
            if time.time() - _last_speed_sample >= 60:  # one aggregate sample per minute
                _last_speed_sample = time.time()
                total = sum(p.get("speed") or 0 for p in _progress.values())
                db.stat("total", "speed", str(int(total)))

    fmt, fmt_sort = youtube.format_opts(quality, media)
    opts = {
        "format": fmt,
        "format_sort": fmt_sort,
        "outtmpl": os.path.join(outdir, f"{job['name']}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "concurrent_fragment_downloads": 1,
        "http_headers": {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
        },
    }
    _apply_impersonation(opts, cfg["ytdlp"])
    if media == "video":
        opts["merge_output_format"] = quality["video_format"]
    else:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": quality["audio_format"],
        }]
    if dl["speed_limit_kbps"]:
        opts["ratelimit"] = dl["speed_limit_kbps"] * 1024
    if cfg["sponsorblock"]["enabled"] and job["provider"] == "youtube":
        opts["postprocessors"] = (opts.get("postprocessors") or []) + [{
            "key": "SponsorBlock", "categories": cfg["sponsorblock"]["categories"],
        }, {
            "key": "ModifyChapters",
            "remove_sponsor_segments": cfg["sponsorblock"]["categories"],
        }]
    proxy = config.proxy_for(job["url"])
    if proxy:
        opts["proxy"] = proxy

    limiter.wait(job["provider"] or "youtube")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(job["url"], download=True)
    if info:
        chosen = info.get("format") or ""
        res = info.get("resolution") or (f"{info.get('height')}p" if info.get("height") else "?")
        log.info("Downloaded format for '%s': %s (%s)", job["name"], chosen or "?", res)
    return outdir


def _run_job(job):
    nzo_id = job["nzo_id"]
    job["status"] = "Downloading"
    db.job_save(job)
    set_status(f"Downloading: {job['name']}")
    try:
        outdir = _ydl_download(job)
        size = sum(os.path.getsize(os.path.join(outdir, f)) for f in os.listdir(outdir))
        job.update({"status": "Completed", "storage": outdir, "bytes_total": size,
                    "bytes_done": size, "completed": int(time.time())})
        db.stat(job["indexer_id"] or "manual", "download_ok", job["name"])
        limiter.reset(job["provider"] or "youtube")
        log.info("Completed '%s' -> %s", job["name"], outdir)
    except _Abort:
        job = db.job_get(nzo_id)
        if job:  # paused (still in DB) vs deleted
            log.info("Aborted '%s' (%s)", job["name"], job["status"])
        return
    except Exception as exc:
        if youtube._is_rate_limit(exc):
            limiter.penalize(job["provider"] or "youtube")
        job.update({"status": "Failed", "fail_message": str(exc)[:500],
                    "completed": int(time.time())})
        db.stat(job["indexer_id"] or "manual", "download_fail", job["name"])
        log_connection_error(log, f"download of '{job['name']}'", job["url"], exc)
    finally:
        _progress.pop(nzo_id, None)
        _speed_hist.pop(nzo_id, None)
        with _cancel_lock:
            _cancel.discard(nzo_id)
        if db.job_get(nzo_id):
            db.job_save(job)
        clear_status()


def _worker():
    log.info("Download worker started")
    while True:
        _wake.wait(timeout=5)
        _wake.clear()
        if config.get()["downloads"]["paused"]:
            continue
        queued = [j for j in db.jobs_active() if j["status"] == "Queued"]
        if not queued:
            continue
        queued.sort(key=lambda j: (-j["priority"], j["position"], j["added"]))
        _run_job(queued[0])
        delay = config.get()["ratelimit"]["download_delay"]
        if delay:
            time.sleep(delay)
        _wake.set()


def start():
    # recover jobs stuck in Downloading after an unclean shutdown
    for j in db.jobs_active():
        if j["status"] == "Downloading":
            j["status"] = "Queued"
            db.job_save(j)
    threading.Thread(target=_worker, daemon=True, name="downloader").start()
