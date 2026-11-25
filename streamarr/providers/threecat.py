import logging
import re
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .. import arr, config
from ..runtime import limiter, log_connection_error

log = logging.getLogger("streamarr.threecat")
API = "https://api.3cat.cat"
PROGRAMS_URL = f"{API}/programestv"
VIDEOS_URL = f"{API}/videos"
MADRID = ZoneInfo("Europe/Madrid")  # data_emissio is local broadcast time, not UTC

CATALOG_TTL = 6 * 3600   # the program catalog has no server-side search: keep it in memory
PAGE_SIZE = 1000
MAX_PAGES = 5            # season pages, so a season can be walked whole
MAX_PROGRAMS = 3
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_UA = "streamarr (+https://github.com/makearr/streamarr)"
RESOLVE_TTL = 24 * 3600  # Sonarr title -> Catalan title, hits and misses alike
FAILURE_TTL = 10 * 60    # a failed Sonarr or Wikidata lookup is retried after this

_catalog_cache = {"at": 0.0, "programs": []}
_resolve_cache = {}  # tvdb id or "sonarr:<title>" -> (expires, Catalan title or None)


def _get(url, params):
    limiter.wait("3cat")
    try:
        r = httpx.get(url, params=params, proxy=config.proxy_for(url), timeout=30)
        if r.status_code in (429, 503):
            limiter.penalize("3cat")
        r.raise_for_status()
    except Exception as exc:
        log_connection_error(log, "3cat API", url, exc)
        raise
    limiter.reset("3cat")
    return r.json()


def _norm(text):
    """Casefold, strip accents, collapse punctuation: 'El Foraster' == 'el-foràster'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.findall(r"[^\W_]+", text))


def _programs():
    if time.time() - _catalog_cache["at"] <= CATALOG_TTL:
        return _catalog_cache["programs"]
    try:
        data = _get(PROGRAMS_URL, {"_format": "json", "version": "2.0"})
    except Exception:
        if not _catalog_cache["programs"]:
            raise
        log.warning("3cat catalog refresh failed — serving the stale catalog")
        return _catalog_cache["programs"]
    letters = ((data.get("resposta") or {}).get("items") or {}).get("lletra") or []
    programs = [p for lt in letters for p in lt.get("item") or []]
    if not programs:  # an empty parse must not replace the catalog or reset the TTL
        log.warning("3cat catalog came back empty — keeping the previous one")
        return _catalog_cache["programs"]
    _catalog_cache.update(at=time.time(), programs=programs)
    return programs


def _match_programs(text, programs):
    """Exact title or slug wins alone; otherwise titles holding every query word as a whole word."""
    query = _norm(text)
    if not query:
        return []
    exact = [p for p in programs if query in (_norm(p.get("titol")), _norm(p.get("nombonic")))]
    if exact:
        return exact[:MAX_PROGRAMS]
    words = set(query.split())
    return [p for p in programs if words <= set(_norm(p.get("titol")).split())][:MAX_PROGRAMS]


def _wikidata_fetch(tvdb):
    """Catalan title of the Wikidata item whose TheTVDB id (P4835) is `tvdb`, or None."""
    kw = {"headers": {"User-Agent": WIKIDATA_UA}, "proxy": config.proxy_for(WIKIDATA_API), "timeout": 10}
    r = httpx.get(WIKIDATA_API, params={"action": "query", "list": "search", "format": "json",
                                        "srsearch": f"haswbstatement:P4835={tvdb}"}, **kw)
    r.raise_for_status()
    hits = (r.json().get("query") or {}).get("search") or []
    if not hits:
        return None
    qid = hits[0]["title"]
    r = httpx.get(WIKIDATA_API, params={"action": "wbgetentities", "ids": qid, "format": "json",
                                        "props": "labels|claims", "languages": "ca"}, **kw)
    r.raise_for_status()
    entity = r.json()["entities"][qid]
    ids = [c["mainsnak"].get("datavalue", {}).get("value") for c in entity.get("claims", {}).get("P4835", [])]
    if str(tvdb) not in ids:  # the search is fuzzy; only trust an exact TVDB id
        return None
    label = (entity.get("labels") or {}).get("ca") or {}
    if label.get("value"):
        return label["value"]
    for c in entity.get("claims", {}).get("P1476", []):  # title, with its language tag
        value = c["mainsnak"].get("datavalue", {}).get("value") or {}
        if value.get("language") == "ca" and value.get("text"):
            return value["text"]
    return None


def _cached(key):
    """(hit, title): a live cache entry for `key`, if any."""
    hit = _resolve_cache.get(key)
    return (True, hit[1]) if hit and time.time() < hit[0] else (False, None)


def _wikidata_title(tvdb):
    found, title = _cached(tvdb)
    if found:
        return title
    ttl = RESOLVE_TTL
    try:
        title = _wikidata_fetch(tvdb)
    except Exception as exc:
        log_connection_error(log, "Wikidata", WIKIDATA_API, exc)
        title, ttl = None, FAILURE_TTL
    _resolve_cache[tvdb] = (time.time() + ttl, title)
    return title


def _resolve_title(text, idx):
    """Catalan title for a Sonarr title: the indexer's manual alias, else Wikidata via TheTVDB."""
    for a in idx.get("aliases") or []:
        if isinstance(a, dict) and _norm(a.get("sonarr")) == _norm(text) and a.get("threecat"):
            log.info("3cat: '%s' -> '%s' via alias", text, a["threecat"])
            return a["threecat"]
    key = f"sonarr:{_norm(text)}"
    found, _ = _cached(key)
    if found:
        return None
    try:
        tvdb = arr.tvdb_id(text)
    except Exception as exc:
        log.warning("3cat: Sonarr lookup for '%s' failed: %s", text, exc)
        _resolve_cache[key] = (time.time() + FAILURE_TTL, None)
        return None
    if not tvdb:
        return None
    title = _wikidata_title(tvdb)
    if title:
        log.info("3cat: '%s' -> '%s' via wikidata (tvdb %s)", text, title, tvdb)
    return title


def _videos(filters, limit, season=None):
    """Newest-first videos. Without a season: one page, capped at limit. With a season: every
    page up to MAX_PAGES, uncapped, so newznab can pick the episode out of the whole season."""
    out, page = [], 1
    while True:
        params = {"_format": "json", "version": "2.0", "tipus_contingut": "PPD",
                  "ordre": "-data_emissio", **filters,
                  "items_pagina": PAGE_SIZE if season else min(limit, PAGE_SIZE), "pagina": page}
        if season:
            params["temporada"] = f"PUTEMP_{season}"
        data = _get(VIDEOS_URL, params).get("resposta") or {}
        out += (data.get("items") or {}).get("item") or []
        pages = int((data.get("paginacio") or {}).get("total_pagines") or 1)
        page += 1
        if page > pages or page > MAX_PAGES or (not season and len(out) >= limit):
            return out if season else out[:limit]


def _season_of(video, requested):
    for t in video.get("temporades") or []:
        m = re.fullmatch(r"PUTEMP_(\d+)", t.get("id") or "")
        if m:
            return int(m.group(1))
    return requested


def _duration(value):
    """'00:28:45:00' (HH:MM:SS:frames) -> seconds."""
    parts = (value or "").split(":")
    if len(parts) < 3 or not all(p.isdigit() for p in parts[:3]):
        return None
    h, m, s = (int(p) for p in parts[:3])
    return h * 3600 + m * 60 + s or None


def _published(value):
    """'25/11/2026 16:05:00' (Madrid local) -> epoch."""
    try:
        return int(datetime.strptime(value, "%d/%m/%Y %H:%M:%S").replace(tzinfo=MADRID).timestamp())
    except (TypeError, ValueError):
        return None


def _to_item(v, idx, program_id=None, requested_season=None, series=None):
    slug = v.get("nom_friendly")
    if not slug:
        return None
    vid = v["id"]
    season = _season_of(v, requested_season)
    capitol = int(v.get("capitol") or 0) or None
    ep = int(v.get("capitol_temporada") or 0)
    title = v.get("permatitle") or v.get("titol") or slug
    has_numbering = bool(season) or ep > 0
    if season and ep > 0:
        # SxxEyy tag so naming.parse_sxxeyy and the newznab season/episode filter can see it
        title = f"S{season:02d}E{ep:02d} - {title}"
    return {
        "id": f"3cat:{vid}",
        "indexer_id": idx["id"],
        "provider": "3cat",
        "series_title": series or v.get("programa") or "3cat",
        "title": title,
        "url": f"https://www.3cat.cat/3cat/{slug}/video/{vid}/",
        "published": _published(v.get("data_emissio")),
        "duration": _duration(v.get("durada")),
        # capitol is series-wide: only usable as the episode number when there is no season
        "ordinal": None if has_numbering else capitol,
        "meta": {"channel": "3cat", "program_id": program_id,
                 "geo": [g.get("id") for g in v.get("geolocalitzacions") or [] if g.get("id")]},
    }


def search(text, idx, limit=100, season=None):
    """Program match on the catalog, then that program's episodes; empty text = latest."""
    if not text:
        log.info("3cat: empty query (arr test / RSS) — serving latest videos")
        videos = _videos({}, limit)
        items = [_to_item(v, idx) for v in videos]
        return [it for it in items if it][:limit]
    matches = _match_programs(text, _programs())
    series = None
    if not matches:
        title = _resolve_title(text, idx)
        if title:
            matches = _match_programs(title, _programs())
            series = text  # Sonarr knows the show by its own title, so releases must carry that
    log.info("3cat: '%s' matched %d program(s): %s", text,
             len(matches), ", ".join(p.get("titol", "?") for p in matches) or "-")
    items = []
    for p in matches:
        videos = _videos({"programatv_id": p["id"]}, limit, season)
        items += [_to_item(v, idx, program_id=p["id"], requested_season=season, series=series)
                  for v in videos]
    items = [it for it in items if it]
    return items if season else items[:limit]  # a season goes back whole; newznab filters it
