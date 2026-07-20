import json
import logging
import re

from fastapi import APIRouter, Depends, Request

from .. import VERSION, auth, config, db, downloader

log = logging.getLogger("streamarr.sabnzbd")
router = APIRouter()

CATEGORIES = ["*", "tv", "movies", "music", "books", "audiobooks", "podcasts", "adult"]


def _mb(b):
    return f"{(b or 0) / 1048576:.2f}"


CAT_TYPES = {"tv": ("sonarr",), "movies": ("radarr",), "music": ("lidarr",),
             "books": ("readarr",), "audiobooks": ("readarr",), "adult": ("whisparr",)}


def _default_priority(category):
    """Per-upstream-app default priority: grabs are matched to the pushing app by category."""
    types = CAT_TYPES.get(category or "", ())
    for inst in config.get()["instances"]:
        if inst.get("enabled") and inst["type"] in types and inst.get("default_priority"):
            return int(inst["default_priority"])
    return 0


def _timeleft(eta):
    if not eta:
        return "0:00:00"
    return f"{eta // 3600}:{eta % 3600 // 60:02d}:{eta % 60:02d}"


def _queue_payload():
    cfg = config.get()["downloads"]
    jobs = downloader.queue_snapshot()
    slots = []
    speed = 0
    for i, j in enumerate(jobs):
        left = max((j["bytes_total"] or 0) - (j["bytes_done"] or 0), 0)
        pct = int(j["bytes_done"] / j["bytes_total"] * 100) if j.get("bytes_total") else 0
        speed += j.get("speed") or 0
        slots.append({
            "index": i, "nzo_id": j["nzo_id"], "filename": j["name"],
            "cat": j["category"] or "*", "priority": "Normal",
            "status": "Paused" if cfg["paused"] and j["status"] == "Queued" else j["status"],
            "mb": _mb(j["bytes_total"]), "mbleft": _mb(left),
            "percentage": str(pct), "timeleft": _timeleft(j.get("eta")),
            "size": _mb(j["bytes_total"]) + " MB",
        })
    return {"queue": {
        "version": VERSION, "paused": cfg["paused"],
        "speedlimit_abs": str(cfg["speed_limit_kbps"] * 1024 if cfg["speed_limit_kbps"] else 0),
        "kbpersec": f"{speed / 1024:.2f}", "mbleft": _mb(sum((j["bytes_total"] or 0) - (j["bytes_done"] or 0) for j in jobs)),
        "mb": _mb(sum(j["bytes_total"] or 0 for j in jobs)),
        "noofslots": len(slots), "slots": slots,
    }}


def _history_payload(limit=60):
    slots = []
    for j in db.jobs_history(limit):
        slots.append({
            "nzo_id": j["nzo_id"], "name": j["name"], "category": j["category"] or "*",
            "status": j["status"], "storage": j["storage"] or "",
            "path": j["storage"] or "", "bytes": j["bytes_total"] or 0,
            "fail_message": j["fail_message"] or "",
            "completed": j["completed"] or 0,
            "download_time": 0, "postproc_time": 0, "stage_log": [],
        })
    return {"history": {"version": VERSION, "noofslots": len(slots), "slots": slots}}


@router.api_route("/api", methods=["GET", "POST"])  # SAB default path (urlBase empty)
@router.api_route("/sabnzbd/api", methods=["GET", "POST"])
async def sab_api(request: Request, _=Depends(auth.require_api_key)):
    q = dict(request.query_params)
    mode = q.get("mode", "")
    name = q.get("name", "")
    if mode not in ("queue", "fullstatus", "version"):  # keep the log free of poll noise
        log.info("SABnzbd request from %s: mode=%s name=%s value=%s cat=%s",
                 request.client.host if request.client else "?", mode, name or "-",
                 q.get("value", "-"), q.get("cat", "-"))

    if mode == "version":
        return {"version": VERSION}
    if mode == "get_config":
        cfg = config.get()
        return {"config": {
            "misc": {"complete_dir": cfg["downloads"]["path"], "enable_tv_sorting": 0,
                     "enable_movie_sorting": 0, "enable_date_sorting": 0,
                     "pre_check": 0, "history_retention": "", "history_retention_option": "all"},
            "categories": [{"name": c, "dir": "" if c == "*" else c, "pp": "", "script": ""}
                           for c in CATEGORIES],
            "sorters": [],
        }}
    if mode == "fullstatus":
        return {"status": {"version": VERSION, "paused": config.get()["downloads"]["paused"]}}
    if mode == "queue":
        if name == "pause":
            downloader.pause(q.get("value"))
            return {"status": True}
        if name == "resume":
            downloader.resume(q.get("value"))
            return {"status": True}
        if name == "delete":
            for nzo in (q.get("value") or "").split(","):
                downloader.delete(nzo.strip())
            return {"status": True}
        return _queue_payload()
    if mode == "pause":
        downloader.pause(None)
        return {"status": True}
    if mode == "resume":
        downloader.resume(None)
        return {"status": True}
    if mode == "switch":
        downloader.move(q.get("value"), int(q.get("value2", 0)))
        return {"status": True, "position": q.get("value2"), "priority": 0}
    if mode == "config" and name == "speedlimit":
        v = q.get("value", "0")
        kbps = int(re.sub(r"[^0-9]", "", v) or 0)
        if v.endswith("M"):
            kbps *= 1024
        downloader.set_speed_limit(kbps)
        return {"status": True}
    if mode == "history":
        if name == "delete":
            for nzo in (q.get("value") or "").split(","):
                if nzo.strip() and nzo != "all":
                    db.job_delete(nzo.strip())
            return {"status": True}
        return _history_payload(int(q.get("limit", 60) or 60))
    if mode == "addfile":
        form = await request.form()
        upload = form.get("nzbfile") or form.get("name")
        if hasattr(upload, "read"):
            raw = await upload.read()
            if len(raw) > 1_000_000:
                return {"status": False, "error": "NZB too large"}
            content = raw.decode("utf-8", "ignore")
        else:
            content = str(upload or "")
        return _add_from_nzb(content, q.get("cat") or form.get("cat") or "")
    if mode == "addurl":
        return {"status": False, "error": "addurl is not supported; Streamarr indexers serve pseudo-NZBs via addfile"}
    return {"status": False, "error": f"Unsupported mode '{mode}'"}


def _add_from_nzb(content, category):
    m = re.search(r"STREAMARR:(\{.*\})", content, re.S)
    if not m:
        return {"status": False, "error": "Not a Streamarr pseudo-NZB — this client only accepts NZBs produced by a Streamarr indexer"}
    try:
        p = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {"status": False, "error": "Corrupt Streamarr NZB payload"}
    prio = _default_priority(category)
    media = p.get("media", "video")
    if category in ("music", "audiobooks", "podcasts"):
        media = "audio"  # lidarr/readarr grabs always yield audio files
    log.info("addfile accepted: '%s' (provider=%s, cat=%s, prio=%s, media=%s)",
             p["name"], p.get("provider"), category, prio, media)
    nzo_id = downloader.enqueue(
        name=p["name"], url=p["url"], category=category,
        indexer_id=p.get("indexer_id"), provider=p.get("provider"), media=media, priority=prio)
    return {"status": True, "nzo_ids": [nzo_id]}
