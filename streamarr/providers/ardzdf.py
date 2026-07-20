import logging

import httpx

from .. import config
from ..runtime import limiter, log_connection_error
from . import mediathek

log = logging.getLogger("streamarr.ardzdf")

ARD_API = "https://api.ardmediathek.de/search-system/search/vods"
ZDF_API = "https://zdf-prod-futura.zdf.de/mediathekV2/search"

ARD_CHANNELS = ("ARD", "Das Erste", "BR", "HR", "MDR", "NDR", "RBB", "SR", "SWR", "WDR", "ONE", "ARD-alpha", "tagesschau24")
ZDF_CHANNELS = ("ZDF", "3Sat", "ZDF-tivi", "ZDFinfo", "ZDFneo")


def _ard_direct(text, idx, limit):
    limiter.wait("ard")
    r = httpx.get(ARD_API, params={"query": text or "", "pageSize": limit},
                  proxy=config.proxy_for(ARD_API), timeout=30)
    if r.status_code in (429, 503):
        limiter.penalize("ard")
    r.raise_for_status()
    limiter.reset("ard")
    data = r.json()
    teasers = data.get("teasers") or data.get("items") or []
    items = []
    for t in teasers:
        tid = t.get("id") or (t.get("links", {}).get("target", {}) or {}).get("id")
        if not tid:
            continue
        show = (t.get("show") or {}).get("title") if isinstance(t.get("show"), dict) else t.get("show")
        items.append({
            "id": f"ard:{tid}",
            "indexer_id": idx["id"],
            "provider": "ard",
            "series_title": show or t.get("publicationService", {}).get("name") or "ARD",
            "title": t.get("longTitle") or t.get("mediumTitle") or t.get("shortTitle") or tid,
            "url": f"https://www.ardmediathek.de/video/{tid}",
            "published": _ts(t.get("broadcastedOn")),
            "duration": int(t.get("duration") or 0) or None,
            "ordinal": None,
            "meta": {"channel": "ARD"},
        })
    return items


def _zdf_direct(text, idx, limit):
    limiter.wait("zdf")
    r = httpx.get(ZDF_API, params={"q": text or ""}, proxy=config.proxy_for(ZDF_API), timeout=30)
    if r.status_code in (429, 503):
        limiter.penalize("zdf")
    r.raise_for_status()
    limiter.reset("zdf")
    data = r.json()
    results = []
    for cluster in data.get("cluster") or []:
        results += cluster.get("teaser") or []
    items = []
    for t in results[:limit]:
        url = t.get("sharingUrl") or t.get("url")
        if not url:
            continue
        if url.startswith("/"):
            url = "https://www.zdf.de" + url
        items.append({
            "id": f"zdf:{t.get('id') or url}",
            "indexer_id": idx["id"],
            "provider": "zdf",
            "series_title": t.get("brandTitle") or "ZDF",
            "title": t.get("titel") or t.get("title") or t.get("headline") or url,
            "published": _ts(t.get("editorialDate")),
            "url": url,
            "duration": int(t.get("length") or 0) or None,
            "ordinal": None,
            "meta": {"channel": "ZDF"},
        })
    return items


def _ts(v):
    if not v:
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def search(broadcaster, text, idx, limit=50):
    """Direct broadcaster search; falls back to MediathekViewWeb filtered by channel."""
    direct, channels = ((_ard_direct, ARD_CHANNELS) if broadcaster == "ard"
                        else (_zdf_direct, ZDF_CHANNELS))
    if not text:
        log.info("%s: empty query (arr test / RSS) — serving latest via MediathekViewWeb",
                 broadcaster.upper())
        return _mvw_filtered(broadcaster, text, idx, channels, limit)
    try:
        items = direct(text, idx, limit)
        if items:
            return items
        log.info("%s direct search returned nothing — falling back to MediathekViewWeb", broadcaster.upper())
    except Exception as exc:
        log_connection_error(log, f"{broadcaster.upper()} direct API", ARD_API if broadcaster == "ard" else ZDF_API, exc)
        log.warning("%s direct API failed — falling back to MediathekViewWeb", broadcaster.upper())
    return _mvw_filtered(broadcaster, text, idx, channels, limit)


def _mvw_filtered(broadcaster, text, idx, channels, limit):
    items = mediathek.search(text, idx["id"], limit=limit * 2)
    out = []
    for it in items:
        ch = (it.get("meta") or {}).get("channel") or ""
        if any(ch.lower().startswith(c.lower()) for c in channels):
            it["provider"] = broadcaster
            out.append(it)
    log.info("%s: %d of %d MediathekViewWeb results match the channel filter",
             broadcaster.upper(), len(out), len(items))
    return out[:limit]
