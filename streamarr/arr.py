import logging
import re
import time
import httpx

from . import config
from .runtime import log_connection_error

log = logging.getLogger("streamarr.arr")

API_VER = {"sonarr": "v3", "radarr": "v3", "lidarr": "v1", "readarr": "v1", "whisparr": "v3", "prowlarr": "v1"}


def _client(inst):
    base = config.normalize_url(inst["url"])
    return httpx.Client(
        base_url=f"{base}/api/{API_VER.get(inst['type'], 'v3')}",
        headers={"X-Api-Key": inst["api_key"]},
        verify=inst.get("verify_ssl", True),
        proxy=config.proxy_for(base),
        timeout=20,
    )


def test(inst):
    """Return (ok, message). Verbose failure diagnostics per requirement."""
    base = config.normalize_url(inst["url"])
    try:
        with _client(inst) as c:
            r = c.get("/system/status")
            r.raise_for_status()
            data = r.json()
            return True, f"{data.get('appName', inst['type'])} {data.get('version', '')}".strip()
    except httpx.ConnectError as exc:
        log_connection_error(log, f"{inst['name']} (TCP connect)", base, exc)
        return False, f"TCP connection failed to {base} — host down, port closed, or DNS failure: {exc}"
    except httpx.TimeoutException as exc:
        log_connection_error(log, f"{inst['name']} (timeout)", base, exc)
        return False, f"Timeout connecting to {base}: {exc}"
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        log_connection_error(log, f"{inst['name']} (HTTP {code})", base, exc)
        if code == 401:
            return False, f"Connected to {base} but the API key was rejected (HTTP 401)"
        return False, f"Connected to {base} but got HTTP {code}"
    except Exception as exc:
        log_connection_error(log, inst["name"], base, exc)
        return False, f"{type(exc).__name__}: {exc}"


def configure(inst, indexer, own_url=None):
    """Create/update indexer + download client, trying URL candidates until the arr accepts one.

    Arr apps validate reachability on create/update, so an HTTP-400 rejection of a candidate
    means "this URL doesn't reach Streamarr from there" — the next candidate is tried before
    any user interaction is required."""
    last = None
    for cand in config.public_url_candidates(own_url or inst.get("own_url") or ""):
        try:
            res = _configure_with(inst, indexer, cand)
        except Exception as exc:
            log_connection_error(log, f"auto-configure of {inst['name']}", inst["url"], exc)
            return [{"item": inst["name"], "ok": False, "detail": f"{type(exc).__name__}: {exc}"}]
        if all(r["ok"] for r in res):
            for r in res:
                r["used_url"] = cand
            log.info("Auto-configure of %s succeeded via %s", inst["name"], cand)
            return res
        last = res
        if not any("HTTP 400" in (r.get("detail") or "") for r in res if not r.get("ok")):
            break  # auth/network error against the arr itself — retrying other URLs won't help
        log.info("Candidate %s rejected by %s — trying next", cand, inst["name"])
    return last or []


def _configure_with(inst, indexer, own_url):
    cfg = config.get()
    api_key = cfg["streamarr"]["api_key"]
    own = own_url.rstrip("/")
    from urllib.parse import urlparse
    parsed = urlparse(own)
    results = []

    with _client(inst) as c:
        # --- indexer ---
        idx_name = f"Streamarr ({indexer['name']})"
        payload = {
            "name": idx_name,
            "implementation": "Newznab",
            "configContract": "NewznabSettings",
            "enableRss": True,
            "protocol": "usenet",
            "priority": 25,
            "fields": [
                {"name": "baseUrl", "value": f"{own}/newznab/{indexer['id']}"},
                {"name": "apiPath", "value": "/api"},
                {"name": "apiKey", "value": api_key},
                {"name": "categories", "value": indexer["categories"]},
            ],
        }
        if inst["type"] == "prowlarr":
            payload.update({"appProfileId": 1, "enable": True,
                            "fields": payload["fields"] + [{"name": "vipExpiration", "value": ""}]})
        else:
            payload.update({"enableAutomaticSearch": True, "enableInteractiveSearch": True})
        results.append(_upsert(c, "/indexer", idx_name, payload))

        # --- download client ---
        dc_name = "Streamarr"
        dc_payload = {
            "name": dc_name,
            "implementation": "Sabnzbd",
            "configContract": "SabnzbdSettings",
            "enable": True,
            "protocol": "usenet",
            "priority": 1,
            "fields": [
                {"name": "host", "value": parsed.hostname},
                {"name": "port", "value": parsed.port or 8585},
                {"name": "useSsl", "value": parsed.scheme == "https"},
                {"name": "urlBase", "value": "sabnzbd"},
                {"name": "apiKey", "value": api_key},
                {"name": "username", "value": ""},
                {"name": "password", "value": ""},
            ] + _category_fields(inst["type"]),
        }
        results.append(_upsert(c, "/downloadclient", dc_name, dc_payload))
    return results


def _category_fields(arr_type):
    """SABnzbd settings field names differ per arr application."""
    m = {
        "sonarr": ("tvCategory", "tv", "recentTvPriority", "olderTvPriority"),
        "whisparr": ("tvCategory", "adult", "recentTvPriority", "olderTvPriority"),
        "radarr": ("movieCategory", "movies", "recentMoviePriority", "olderMoviePriority"),
        "lidarr": ("musicCategory", "music", "recentMusicPriority", "olderMusicPriority"),
        "readarr": ("bookCategory", "books", "recentBookPriority", "olderBookPriority"),
    }
    if arr_type not in m:
        return []
    cat_field, cat, recent, older = m[arr_type]
    return [{"name": cat_field, "value": cat},
            {"name": recent, "value": -100},
            {"name": older, "value": -100}]


def _upsert(c, path, name, payload):
    r0 = c.get(path)
    r0.raise_for_status()
    existing = r0.json()
    match = next((e for e in existing if e.get("name") == name), None)
    if match:
        payload["id"] = match["id"]
        r = c.put(f"{path}/{match['id']}", json=payload)
        action = "updated"
    else:
        r = c.post(path, json=payload)
        action = "created"
    if r.status_code >= 400:
        log.error("Auto-configure %s '%s' failed: HTTP %s %s", path, name, r.status_code, r.text[:300])
        return {"item": name, "ok": False, "detail": f"HTTP {r.status_code}: {r.text[:200]}"}
    log.info("Auto-configure: %s '%s' on remote instance", action, name)
    return {"item": name, "ok": True, "detail": action}


def sync_instance(inst):
    """Push indexer + download client config per the instance's auto_configure settings."""
    from . import indexers as _indexers
    cfg = config.get()
    wanted = inst.get("indexer_ids") or [i["id"] for i in cfg["indexers"] if i["enabled"]]
    results = []
    for iid in wanted:
        idx = _indexers.get_indexer(iid)
        if not idx or not idx["enabled"]:
            continue
        try:
            results += configure(inst, idx, own_url=inst.get("own_url") or None)
        except Exception as exc:
            log.error("Auto-sync of '%s' -> %s failed: %s", iid, inst["name"], exc)
            results.append({"item": f"{inst['name']}/{iid}", "ok": False, "detail": str(exc)})
    return results


def sync_all():
    for inst in config.get()["instances"]:
        if inst.get("enabled") and inst.get("auto_configure"):
            log.info("Auto-sync: configuring %s", inst["name"])
            sync_instance(inst)


def has_release(inst, title):
    """True if the arr already has a file for the episode(s) this release title parses to."""
    with _client(inst) as c:
        r = c.get("/parse", params={"title": title})
        if r.status_code != 200:
            return False
        data = r.json() or {}
        eps = data.get("episodes") or []
        return bool(eps) and all(e.get("episodeFileId") or e.get("hasFile") for e in eps)


_series_cache = {}  # inst url -> (ts, [series])


def _norm(t):
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def _title_match(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    ta, tb = set(na.split()), set(nb.split())
    return len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.6


def _series_episodes(inst, series_q):
    """(series, episodes) for a series matched by title, with a 5-minute series cache."""
    base = config.normalize_url(inst["url"])
    with _client(inst) as c:
        cached = _series_cache.get(base)
        if not cached or time.time() - cached[0] > 300:
            r = c.get("/series")
            r.raise_for_status()
            cached = (time.time(), r.json())
            _series_cache[base] = cached
        series = next((s for s in cached[1] if _title_match(s.get("title"), series_q)), None)
        if not series:
            log.info("Arr lookup: no series on %s matches '%s'", inst["name"], series_q)
            return None, []
        r = c.get("/episode", params={"seriesId": series["id"]})
        r.raise_for_status()
        return series, r.json()


def find_episode(inst, series_q, season, ep):
    """Look up season/episode on the arr; returns {series, title, airDate} or None."""
    series, eps = _series_episodes(inst, series_q)
    if not series:
        return None
    for e in eps:
        if e.get("seasonNumber") == season and e.get("episodeNumber") == ep:
            log.info("Arr lookup: %s S%02dE%02d = '%s' (air %s)", series["title"],
                     season, ep, e.get("title"), e.get("airDate"))
            return {"series": series["title"], "title": e.get("title") or "",
                    "airDate": e.get("airDate")}
    log.info("Arr lookup: %s has no S%02dE%02d", series["title"], season, ep)
    return None


def season_episodes(inst, series_q, season):
    """All episodes of one season: [{series, title, airDate, episode}]."""
    series, eps = _series_episodes(inst, series_q)
    if not series:
        return []
    out = [{"series": series["title"], "title": e.get("title") or "",
            "airDate": e.get("airDate"), "episode": e.get("episodeNumber")}
           for e in eps if e.get("seasonNumber") == season and e.get("episodeNumber")]
    log.info("Arr lookup: %s season %s has %d episode(s)", series["title"], season, len(out))
    return out
