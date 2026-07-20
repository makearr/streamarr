import io
import json
import os
import re
import shutil
import time
import zipfile

from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from .. import VERSION, arr, auth, config, db, downloader, indexers, maintenance
from ..runtime import LOG_BUFFER, get_status, limiter

router = APIRouter(prefix="/ui")


# --- auth ---

@router.get("/auth/state")
def auth_state(request: Request):
    return {"mode": auth.mode(), "setup_needed": auth.needs_setup(),
            "authenticated": auth.check_session(request),
            "local": auth.is_local(request),
            "pw_scheme": auth.pw_scheme()}


@router.post("/auth/setup")
def auth_setup(request: Request, body: dict = Body(...)):
    """Enable forms login. No password requirements — the user's box, the user's rules."""
    username, password = body.get("username", "").strip(), body.get("password", "")
    if not username or not password:
        raise HTTPException(400, "Username and password must not be empty")
    existing = config.get()["streamarr"]["auth"]
    if existing.get("password_hash") and not auth.check_session(request):
        # an account exists: changing it requires a valid session (in 'local' mode a
        # local-network requester counts, same as every other /ui endpoint)
        if auth.mode() == "forms" or not auth.is_local(request):
            raise HTTPException(403, "Login required to change the existing account")
    a = dict(config.get()["streamarr"]["auth"])
    a.update({"mode": "forms", "username": username, "password_hash": auth.store_hash(password)})
    config.update("streamarr", {"auth": a})
    return _login_response(username, request)


@router.post("/auth/mode", dependencies=[Depends(auth.require_ui)])
def auth_set_mode(body: dict = Body(...)):
    m = body.get("mode")
    if m not in ("none", "local", "forms"):
        raise HTTPException(400, "mode must be none, local or forms")
    a = dict(config.get()["streamarr"]["auth"])
    if m == "forms" and not (a["username"] and a["password_hash"]):
        raise HTTPException(400, "Create the account first (Settings → Security)")
    a["mode"] = m
    config.update("streamarr", {"auth": a})
    return {"ok": True, "mode": m}


@router.post("/apikey/rotate", dependencies=[Depends(auth.require_ui)])
def apikey_rotate():
    import secrets as _secrets
    key = _secrets.token_hex(16)
    config.update("streamarr", {"api_key": key})
    from .. import arr as _arr
    import threading
    threading.Thread(target=_arr.sync_all, daemon=True).start()  # re-push to auto-configured arrs
    return {"ok": True, "api_key": _mask_key(key)}


@router.post("/auth/login")
def auth_login(request: Request, body: dict = Body(...)):
    a = config.get()["streamarr"]["auth"]
    time.sleep(0.3)  # dampen brute force
    ok, upgraded = (False, None)
    if body.get("username") == a["username"]:
        ok, upgraded = auth.verify_login(body.get("password", ""), a["password_hash"])
    if not ok:
        raise HTTPException(401, "Invalid username or password")
    if upgraded:  # legacy hash -> sha2 transport scheme
        a2 = dict(a)
        a2["password_hash"] = upgraded
        config.update("streamarr", {"auth": a2})
    return _login_response(a["username"], request)


def _login_response(username, request=None):
    resp = JSONResponse({"ok": True, "username": username})
    secure = bool(request) and (request.url.scheme == "https"
                                or request.headers.get("x-forwarded-proto") == "https")
    resp.set_cookie(auth.SESSION_COOKIE, auth.create_session(), max_age=auth.SESSION_MAX_AGE,
                    httponly=True, samesite="lax", secure=secure)
    return resp


@router.post("/auth/logout")
def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


# --- status / system ---

@router.get("/status", dependencies=[Depends(auth.require_ui)])
def status():
    return {
        "version": VERSION,
        "status": get_status(),
        "paused": config.get()["downloads"]["paused"],
        "speed_limit_kbps": config.get()["downloads"]["speed_limit_kbps"],
        "backoff": limiter.state(),
        "ytdlp_version": maintenance.ytdlp_version(),
    }


@router.get("/health/instances", dependencies=[Depends(auth.require_ui)])
def health_instances():
    out = []
    for inst in config.get()["instances"]:
        if not inst["enabled"]:
            out.append({"name": inst["name"], "ok": None, "detail": "disabled"})
            continue
        ok, detail = arr.test(inst)
        out.append({"name": inst["name"], "type": inst["type"], "ok": ok, "detail": detail})
    return out


# --- queue / history ---

@router.get("/queue", dependencies=[Depends(auth.require_ui)])
def queue():
    return {"paused": config.get()["downloads"]["paused"], "jobs": downloader.queue_snapshot()}


@router.get("/history", dependencies=[Depends(auth.require_ui)])
def history(limit: int = 20, offset: int = 0):
    return {"total": db.jobs_history_count(), "limit": limit, "offset": offset,
            "items": db.jobs_history(min(limit, 100), max(offset, 0))}


@router.post("/queue/{action}", dependencies=[Depends(auth.require_ui)])
def queue_action(action: str, body: dict = Body(default={})):
    nzo_id = body.get("nzo_id")
    if action == "pause":
        downloader.pause(nzo_id)
    elif action == "resume":
        downloader.resume(nzo_id)
    elif action == "delete":
        downloader.delete(nzo_id)
    elif action == "move":
        downloader.move(nzo_id, int(body.get("position", 0)))
    elif action == "speedlimit":
        downloader.set_speed_limit(int(body.get("kbps", 0)))
    elif action == "priority":
        j = db.job_get(body.get("nzo_id") or "")
        if not j:
            raise HTTPException(404, "Job not found")
        j["priority"] = int(body.get("priority", 0))
        db.job_save(j)
    else:
        raise HTTPException(400, f"Unknown action '{action}'")
    return {"ok": True}


# --- manual search + grab ---

@router.get("/search", dependencies=[Depends(auth.require_ui)])
def manual_search(indexer_id: str, q: str = "", limit: int = 50, season: int = 0, ep: int = 0):
    idx = indexers.get_indexer(indexer_id)
    if not idx:
        raise HTTPException(404, "Unknown indexer")
    try:
        items = indexers.search(idx, q, limit=limit)
    except Exception as exc:
        raise HTTPException(502, f"Upstream provider error: {exc}")
    if season and ep:
        from .newznab import _filter_episode
        items, _ = _filter_episode(idx, items, season, ep)
    from .. import naming
    tag = naming.quality_tag(config.quality_for(idx), idx["media"])
    for it in items:
        it["release_title"] = naming.release_title(it, idx["naming"], tag)
        if isinstance(it.get("meta"), str):
            it["meta"] = json.loads(it["meta"] or "{}")
    return items


@router.post("/grab", dependencies=[Depends(auth.require_ui)])
def manual_grab(body: dict = Body(...)):
    idx = indexers.get_indexer(body["indexer_id"])
    item = db.cache_get(body["item_id"])
    if not idx or not item:
        raise HTTPException(404, "Item not found in cache")
    from .. import naming
    from ..providers import youtube as _yt
    item = _yt.ensure_exact_date(item)
    tag = naming.quality_tag(config.quality_for(idx), idx["media"])
    name = naming.release_title(item, idx["naming"], tag)
    nzo_id = downloader.enqueue(name=name, url=item["url"], category=body.get("category", ""),
                                indexer_id=idx["id"], provider=item["provider"], media=idx["media"])
    return {"ok": True, "nzo_id": nzo_id}


@router.post("/download", dependencies=[Depends(auth.require_ui)])
def manual_download(body: dict = Body(...)):
    """Direct download of any yt-dlp-supported URL."""
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "URL required")
    name = (body.get("name") or "").strip() or url.rstrip("/").rsplit("/", 1)[-1][:80]
    nzo_id = downloader.enqueue(name=name, url=url, category=body.get("category", ""),
                                indexer_id="manual", provider="manual",
                                media=body.get("media", "video"))
    return {"ok": True, "nzo_id": nzo_id}


@router.post("/subscriptions/run", dependencies=[Depends(auth.require_ui)])
def subscriptions_run():
    from .. import subscriptions
    import threading
    threading.Thread(target=subscriptions.process, kwargs={"force": True}, daemon=True).start()
    return {"ok": True, "detail": "Subscription check started in the background"}


DOMAIN_PRESETS = [  # domain fragment -> (provider, site_preset, site folder name)
    ("youtube.com", ("youtube", "", "youtube")), ("youtu.be", ("youtube", "", "youtube")),
    ("music.youtube.com", ("youtube", "", "youtube-music")),
    ("pornhub.com", ("site", "pornhub", "pornhub")), ("xhamster.com", ("site", "xhamster", "xhamster")),
    ("xvideos.com", ("site", "xvideos", "xvideos")), ("youporn.com", ("site", "youporn", "youporn")),
    ("redtube.com", ("site", "redtube", "redtube")), ("spankbang.com", ("site", "spankbang", "spankbang")),
    ("eporner.com", ("site", "eporner", "eporner")), ("tnaflix.com", ("site", "tnaflix", "tnaflix")),
    ("soundcloud.com", ("site", "soundcloud", "soundcloud")), ("bandcamp.com", ("site", "bandcamp", "bandcamp")),
    ("mixcloud.com", ("site", "mixcloud", "mixcloud")), ("audiomack.com", ("site", "audiomack", "audiomack")),
    ("twitch.tv", ("site", "twitch", "twitch")), ("dailymotion.com", ("site", "dailymotion", "dailymotion")),
    ("rumble.com", ("site", "rumble", "rumble")), ("bilibili.com", ("site", "bilibili", "bilibili")),
    ("nicovideo.jp", ("site", "nicovideo", "nicovideo")), ("twitter.com", ("site", "twitter", "twitter")),
    ("x.com", ("site", "twitter", "twitter")), ("instagram.com", ("site", "instagram", "instagram")),
    ("reddit.com", ("site", "reddit", "reddit")), ("archive.org", ("site", "archiveorg", "archive")),
    ("odysee.com", ("site", "odysee", "odysee")), ("bitchute.com", ("site", "bitchute", "bitchute")),
    ("vimeo.com", ("site", "vimeo", "vimeo")), ("tiktok.com", ("site", "tiktok", "tiktok")),
    ("ted.com", ("site", "ted", "ted")), ("peertube", ("site", "peertube", "peertube")),
]

AUDIO_PRESETS = {"soundcloud", "bandcamp", "mixcloud", "audiomack", "lastfm", "podcast", "audiobook"}


_SITE_FOLDERS = {"youtube", "youtube-music", "pornhub", "xhamster", "xvideos", "youporn",
                 "redtube", "spankbang", "eporner", "tnaflix", "soundcloud", "bandcamp",
                 "mixcloud", "audiomack", "twitch", "dailymotion", "rumble", "bilibili",
                 "nicovideo", "twitter", "instagram", "reddit", "archive", "odysee",
                 "bitchute", "vimeo", "tiktok", "ted", "peertube", "mediathek"}


def _downloads_root():
    """The base download directory, without a trailing per-site folder. If the configured
    downloads path already ends in a known site folder (e.g. /downloads/youtube), use its
    parent so subscriptions nest as <root>/<site>/<name> rather than doubling the site."""
    p = config.get()["downloads"]["path"].rstrip("/")
    if os.path.basename(p).lower() in _SITE_FOLDERS:
        return os.path.dirname(p) or "/downloads"
    return p or "/downloads"


def _guess_sub(url):
    """Pinchflat-style: derive provider/preset/title/path from nothing but the URL."""
    from urllib.parse import urlparse
    u = urlparse(url)
    host = (u.netloc or "").lower().replace("www.", "")
    provider, preset, folder = "site", "custom", host.split(".")[0] or "site"
    for frag, cfg in DOMAIN_PRESETS:
        if frag in host:
            provider, preset, folder = cfg
            break
    segs = [seg for seg in u.path.split("/") if seg and seg.lower() not in
            ("model", "models", "channel", "channels", "user", "users", "c", "pornstar",
             "videos", "playlist", "watch", "@")]
    title = (segs[-1] if segs else folder).lstrip("@")
    title = re.sub(r"[^A-Za-z0-9 ._-]", "", title)[:60] or folder
    sid = re.sub(r"[^a-z0-9_-]", "", title.lower().replace(" ", "-"))[:32] or folder
    media = "audio" if preset in AUDIO_PRESETS else "video"
    root = _downloads_root()
    path = os.path.join(root, folder, title)
    return {"id": sid, "title": title, "url": url, "provider": provider, "site_preset": preset,
            "media": media, "naming": "date", "category": "", "path": path,
            "interval_minutes": 60, "check_arr": "", "priority": 0, "initial": "new_only",
            "enabled": True}


@router.post("/subs/quick", dependencies=[Depends(auth.require_ui)])
def sub_quick_add(body: dict = Body(...)):
    """Hand over a URL — everything else is guessed; the first check starts immediately."""
    url = (body.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "A full channel/playlist/model URL is required")
    sub = _guess_sub(url)
    if body.get("backlog"):
        sub["initial"] = "backlog"
    subs = list(config.get()["subs"])
    if any(x["id"] == sub["id"] for x in subs):
        sub["id"] = f"{sub['id'][:28]}-{len(subs)+1}"
    subs.append(config._merge(config.SUB_DEFAULTS, sub))
    config.update("subs", subs)
    from .. import subscriptions
    import threading
    threading.Thread(target=subscriptions.process, kwargs={"force": True}, daemon=True).start()
    return sub


@router.post("/subs/run", dependencies=[Depends(auth.require_ui)])
def sub_run_one(body: dict = Body(...)):
    sid = body.get("id")
    sub = next((x for x in config.get()["subs"] if x["id"] == sid), None)
    if not sub:
        raise HTTPException(404, "Subscription not found — save it first")
    from .. import subscriptions
    import threading
    threading.Thread(target=subscriptions.process_one, args=(sid,),
                     kwargs={"force": True}, daemon=True).start()
    return {"ok": True, "detail": f"Checking '{sub['title']}' now…"}


@router.get("/subs", dependencies=[Depends(auth.require_ui)])
def list_subs():
    return config.get()["subs"]


@router.post("/subs", dependencies=[Depends(auth.require_ui)])
def save_subs(body: list = Body(...)):
    seen = set()
    merged = []
    for sub in body:
        sid = (sub.get("id") or "").strip()
        if not re.fullmatch(r"[a-z0-9_-]{2,32}", sid):
            raise HTTPException(400, f"Subscription id '{sid}' must be 2-32 chars of a-z 0-9 _ -")
        if sid in seen:
            raise HTTPException(400, f"Duplicate subscription id '{sid}'")
        seen.add(sid)
        if sub.get("provider") not in ("youtube", "site"):
            raise HTTPException(400, f"Subscription '{sid}': provider must be youtube or site")
        if not (sub.get("url") or "").strip():
            raise HTTPException(400, f"Subscription '{sid}': URL required")
        merged.append(config._merge(config.SUB_DEFAULTS, sub))
    config.update("subs", merged)
    return merged


@router.get("/presets", dependencies=[Depends(auth.require_ui)])
def site_presets():
    from ..providers.site import SITE_PRESETS
    return {"providers": {
        "youtube": {"name": "YouTube", "hint": "Curated channel list with stable episode numbers; optional broad search."},
        "mediathek": {"name": "Mediathek (all German broadcasters)", "hint": "Live search across ARD/ZDF/… via MediathekViewWeb."},
        "ard": {"name": "ARD (direct)", "hint": "Direct search against the ARD Mediathek API (MediathekViewWeb fallback)."},
        "zdf": {"name": "ZDF (direct)", "hint": "Direct search against the ZDF Mediathek API (MediathekViewWeb fallback)."},
        "site": {"name": "Streaming site (yt-dlp)", "hint": "Preset-based generic provider."},
    }, "sites": SITE_PRESETS,
    "categories": [[2000, "Movies"], [3000, "Audio / Music"], [3010, "Audio / MP3"],
                   [3030, "Audiobooks"], [5000, "TV"], [6000, "Adult"],
                   [7000, "Books"], [7020, "eBooks"]],
    "url_candidates": config.public_url_candidates()}


@router.get("/stats/timeseries", dependencies=[Depends(auth.require_ui)])
def stats_timeseries(range: str = "24h"):
    hours = {"24h": 24, "7d": 168, "30d": 720}.get(range, 24)
    return db.stats_timeseries(hours, 48)


@router.get("/dashboard", dependencies=[Depends(auth.require_ui)])
def dashboard():
    cfg = config.get()
    jobs = downloader.queue_snapshot()
    hist = db.jobs_history(8)
    return {
        "queue": jobs[:8], "queue_total": len(jobs),
        "paused": cfg["downloads"]["paused"],
        "history": hist,
        "indexers": [{"id": i["id"], "name": i["name"], "provider": i["provider"],
                      "site_preset": i.get("site_preset", ""), "enabled": i["enabled"]}
                     for i in cfg["indexers"]],
        "instances": [{"name": i["name"], "type": i["type"], "enabled": i["enabled"]}
                      for i in cfg["instances"]],
        "stats": db.stats_summary(),
    }


MASK = "********"


def _mask_key(k):
    return f"{k[:4]}…{k[-4:]}" if k else ""


@router.post("/apikey", dependencies=[Depends(auth.require_ui)])
def apikey_reveal():
    """Full key only on explicit request (copy button) — never in regular settings payloads."""
    return {"api_key": config.get()["streamarr"]["api_key"]}


# --- settings ---

SETTING_SECTIONS = ["downloads", "quality", "sponsorblock", "ratelimit", "cache", "ytdlp", "proxy", "subscriptions"]


@router.get("/settings", dependencies=[Depends(auth.require_ui)])
def get_settings():
    cfg = config.get()
    out = {s: cfg[s] for s in SETTING_SECTIONS}
    out["streamarr"] = {"port": cfg["streamarr"]["port"], "base_url": cfg["streamarr"]["base_url"],
                        "public_url": cfg["streamarr"].get("public_url", ""),
                        "api_key": _mask_key(cfg["streamarr"]["api_key"]),
                        "log_level": cfg["streamarr"]["log_level"]}
    out["proxy"] = dict(out["proxy"])
    if out["proxy"].get("password"):
        out["proxy"]["password"] = MASK
    out["url_guess"] = config.public_url_candidates()[0]
    return out


@router.post("/settings/{section}", dependencies=[Depends(auth.require_ui)])
def save_settings(section: str, body: dict = Body(...)):
    if section == "streamarr":
        allowed = {k: v for k, v in body.items() if k in ("public_url", "log_level", "port")}
        if "port" in allowed:
            try:
                allowed["port"] = int(allowed["port"])
                assert 1 <= allowed["port"] <= 65535
            except (ValueError, AssertionError):
                raise HTTPException(400, "Port must be 1-65535")
        if "public_url" in allowed:
            allowed["public_url"] = config.normalize_url(allowed["public_url"]) if allowed["public_url"] else ""
        return config.update("streamarr", allowed)
    if section not in SETTING_SECTIONS:
        raise HTTPException(400, "Unknown settings section")
    if section == "proxy" and body.get("password") == MASK:
        body = dict(body)
        body["password"] = config.get()["proxy"]["password"]  # sentinel = keep stored
    return config.update(section, body)


# --- indexers ---

@router.get("/indexers", dependencies=[Depends(auth.require_ui)])
def list_indexers():
    return config.get()["indexers"]


@router.post("/indexers", dependencies=[Depends(auth.require_ui)])
def save_indexers(body: list = Body(...)):
    seen = set()
    for idx in body:
        iid = (idx.get("id") or "").strip()
        if not re.fullmatch(r"[a-z0-9_-]{2,32}", iid):
            raise HTTPException(400, f"Indexer id '{iid}' must be 2-32 chars of a-z 0-9 _ -")
        if iid in seen:
            raise HTTPException(400, f"Duplicate indexer id '{iid}'")
        seen.add(iid)
        if idx.get("provider") not in ("youtube", "mediathek", "ard", "zdf", "site"):
            raise HTTPException(400, f"Unknown provider on '{iid}'")
        idx = config._merge(config.INDEXER_DEFAULTS, idx)
    merged = [config._merge(config.INDEXER_DEFAULTS, i) for i in body]
    config.update("indexers", merged)
    return merged


# --- arr instances ---

@router.get("/instances", dependencies=[Depends(auth.require_ui)])
def list_instances():
    out = []
    for inst in config.get()["instances"]:
        inst = dict(inst)
        if inst.get("api_key"):
            inst["api_key"] = MASK
        out.append(inst)
    return out


def _restore_instance_secrets(insts):
    """MASK sentinels from the browser are replaced with the stored keys (matched by name)."""
    stored = {i["name"]: i for i in config.get()["instances"]}
    for inst in insts:
        if inst.get("api_key") == MASK:
            inst["api_key"] = stored.get(inst.get("name"), {}).get("api_key", "")
    return insts


@router.post("/instances", dependencies=[Depends(auth.require_ui)])
def save_instances(body: list = Body(...)):
    merged = []
    for inst in _restore_instance_secrets(body):
        if inst.get("type") not in arr.API_VER:
            raise HTTPException(400, f"Unknown instance type '{inst.get('type')}'")
        merged.append(config._merge(config.INSTANCE_DEFAULTS, inst))
    config.update("instances", merged)
    import threading
    threading.Thread(target=arr.sync_all, daemon=True).start()
    return list_instances()


@router.post("/instances/test", dependencies=[Depends(auth.require_ui)])
def test_instance(body: dict = Body(...)):
    inst = config._merge(config.INSTANCE_DEFAULTS, _restore_instance_secrets([body])[0])
    ok, detail = arr.test(inst)
    return {"ok": ok, "detail": detail}


@router.post("/instances/configure", dependencies=[Depends(auth.require_ui)])
def configure_instance(body: dict = Body(...)):
    inst = next((i for i in config.get()["instances"] if i["name"] == body.get("name")), None)
    idx = indexers.get_indexer(body.get("indexer_id", ""))
    if not inst or not idx:
        raise HTTPException(404, "Instance or indexer not found")
    try:
        results = arr.configure(inst, idx, own_url=body.get("own_url"))
    except Exception as exc:
        raise HTTPException(502, f"Auto-configure failed: {exc}")
    return results


# --- stats ---

@router.get("/stats", dependencies=[Depends(auth.require_ui)])
def stats():
    return {"summary": db.stats_summary(), "timeline": db.stats_timeline(14)}


# --- logs ---

@router.get("/logs", dependencies=[Depends(auth.require_ui)])
def logs(level: str = "", after: int = 0, limit: int = 500):
    order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    minimum = order.get(level.upper(), 0)
    out = [e for e in LOG_BUFFER if e["seq"] > after and order.get(e["level"], 1) >= minimum]
    return out[-limit:]


# --- maintenance ---

@router.post("/ytdlp/update", dependencies=[Depends(auth.require_ui)])
def ytdlp_update():
    ok, changed, detail = maintenance.update_ytdlp()
    if ok and changed and config.get()["ytdlp"].get("restart_after_update", True):
        maintenance.restart_when_idle("manual yt-dlp update")
        detail += " — restarting once downloads finish"
    return {"ok": ok, "changed": changed, "detail": detail}


# --- backup / restore ---

@router.get("/backup", dependencies=[Depends(auth.require_ui)])
def backup():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fname in ("config.yml", "streamarr.db"):
            path = os.path.join(config.CONFIG_DIR, fname)
            if os.path.exists(path):
                z.write(path, fname)
    buf.seek(0)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(buf, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="streamarr_backup_{stamp}.zip"'})


@router.post("/restore", dependencies=[Depends(auth.require_ui)])
async def restore(file: UploadFile):
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "Backup exceeds the 200 MB limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            if "config.yml" not in names:
                raise HTTPException(400, "Backup zip must contain config.yml")
            for fname in ("config.yml", "streamarr.db"):
                if fname in names:
                    with z.open(fname) as src, open(os.path.join(config.CONFIG_DIR, fname), "wb") as dst:
                        shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile:
        raise HTTPException(400, "Not a valid zip file")
    config.load()
    return {"ok": True, "detail": "Restored — restart the container to fully apply"}
