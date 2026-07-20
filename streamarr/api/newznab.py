import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from .. import arr, auth, config, db, indexers, naming
from ..providers import youtube

log = logging.getLogger("streamarr.newznab")
router = APIRouter()


def _base(request: Request):
    cfg = config.get()["streamarr"]
    return f"{request.url.scheme}://{request.url.netloc}{cfg['base_url']}"


@router.get("/newznab/{indexer_id}/api")
def newznab_api(indexer_id: str, request: Request, _=Depends(auth.require_api_key)):
    idx = indexers.get_indexer(indexer_id)
    if not idx or not idx["enabled"]:
        raise HTTPException(404, "Unknown or disabled indexer")
    q = request.query_params
    t = q.get("t", "caps")
    log.info("Newznab request from %s: indexer=%s t=%s q='%s' season=%s ep=%s cat=%s",
             request.client.host if request.client else "?", indexer_id,
             t, q.get("q", ""), q.get("season", "-"), q.get("ep", "-"), q.get("cat", "-"))
    base = _base(request)
    if t == "caps":
        return Response(indexers.newznab_caps(idx, base), media_type="application/xml")
    if t in ("search", "tvsearch", "movie", "music", "book"):
        limit = min(int(q.get("limit", 100) or 100), 100)
        text = q.get("q", "") or " ".join(
            v for v in (q.get("artist"), q.get("album"), q.get("author"), q.get("title")) if v)
        offset = int(q.get("offset", 0) or 0)
        try:
            items = indexers.search(idx, text, limit=limit, offset=offset)
        except Exception as exc:
            raise HTTPException(502, f"Upstream provider error: {exc}")
        season, ep = q.get("season"), q.get("ep")
        if season and not ep:
            items = _season_expand(idx, items, text, int(season))
            log.info("Season-only request S%s expanded to %d episode release(s)",
                     season, len(items))
        elif season and ep:
            season, ep = int(season), int(ep)
            hit = None
            if idx["naming"] == "arr":
                hit = _arr_title_match(idx, items, text, season, ep)
            if hit:
                items = [hit]
            else:
                items, matched = _filter_episode(idx, items, season, ep)
                if matched:
                    if idx["naming"] == "arr":
                        for it in items:
                            it["_arr_se"] = (season, ep)
                    log.info("Episode filter S%02dE%02d matched %d item(s)", season, ep, len(items))
                else:
                    # returning unmatched items would make the arr chew on garbage releases
                    log.info("Episode S%02dE%02d not found — returning EMPTY result "
                             "(searched %d items; check sources/naming if this is unexpected)",
                             season, ep, len(items))
                    items = []
        titles = [naming.release_title(it, idx["naming"],
                  naming.quality_tag(config.quality_for(idx), idx["media"])) for it in items[:3]]
        log.info("Newznab response: %d release(s)%s", len(items),
                 (" — e.g. " + " | ".join(titles)) if titles else "")
        xml = indexers.newznab_results(idx, items, base, config.get()["streamarr"]["api_key"])
        return Response(xml, media_type="application/xml")
    raise HTTPException(400, f"Unsupported function t={t}")


def _filter_episode(idx, items, season, ep):
    out = []
    for it in items:
        if idx["naming"] in ("absolute", "arr") and it.get("ordinal"):
            if season == 1 and it["ordinal"] == ep:
                out.append(it)
        else:
            se = naming.parse_sxxeyy(it["title"])
            if se and se == (season, ep):
                out.append(it)
    return (out, True) if out else (items, False)


@router.get("/newznab/{indexer_id}/download/{item_id}.nzb")
def download_nzb(indexer_id: str, item_id: str, request: Request, _=Depends(auth.require_api_key)):
    """Serve a Streamarr pseudo-NZB: valid XML wrapper carrying provider metadata."""
    idx = indexers.get_indexer(indexer_id)
    item = db.cache_get(item_id)
    if not idx or not item:
        raise HTTPException(404, "Release not found in cache")
    item = youtube.ensure_exact_date(item)  # real upload date before the release is named
    meta = item.get("meta") or {}
    if isinstance(meta, str):
        meta = json.loads(meta or "{}")
    if idx["naming"] == "arr" and meta.get("arr_se"):
        item["_arr_se"] = tuple(meta["arr_se"])  # keep the arr-resolved SxxEyy in the job name
    tag = naming.quality_tag(config.quality_for(idx), idx["media"])
    payload = {
        "streamarr": True,
        "indexer_id": indexer_id,
        "item_id": item_id,
        "name": naming.release_title(item, idx["naming"], tag),
        "url": item["url"],
        "provider": item["provider"],
        "media": idx["media"],
    }
    from xml.sax.saxutils import escape as _esc
    import time as _time
    size = (item.get("duration") or 1800) * (250_000 if idx["media"] == "video" else 20_000)
    subject = _esc(f'"{payload["name"]}" yEnc (1/1)', {'"': "&quot;"})
    nzb_date = item.get("published") or int(_time.time())
    seg_id = f"{item_id.replace(':', '.')}@streamarr.local"
    # a structurally valid NZB with one file/segment — arr apps validate this before
    # handing the file to the download client and reject empty wrappers
    nzb = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
<!-- STREAMARR:{json.dumps(payload)} -->
<head>
  <meta type="title">{_esc(payload["name"])}</meta>
</head>
<file poster="Streamarr &lt;streamarr@localhost&gt;" date="{nzb_date}" subject="{subject}">
  <groups>
    <group>alt.binaries.streamarr</group>
  </groups>
  <segments>
    <segment bytes="{size}" number="1">{_esc(seg_id)}</segment>
  </segments>
</file>
</nzb>"""
    fname = payload["name"].encode("ascii", "ignore").decode().replace('"', "")
    return Response(nzb, media_type="application/x-nzb",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.nzb"'})


def _season_expand(idx, items, series_q, season):
    """A whole-season request is translated into single-episode releases."""
    if idx["naming"] == "arr":
        for inst in config.get()["instances"]:
            if not inst.get("enabled") or inst["type"] not in ("sonarr", "whisparr"):
                continue
            try:
                eps = arr.season_episodes(inst, series_q, season)
            except Exception as exc:
                log.warning("Arr season lookup on %s failed: %s", inst["name"], exc)
                continue
            if not eps:
                continue
            out = []
            pool = list(items)
            max_extra = config.get()["ratelimit"].get("season_search_max", 40)
            extra = 0
            for e in eps:
                hit = _pick(pool, e, series_q)
                if not hit and extra < max_extra:
                    extra += 1
                    try:
                        batch = indexers.search(idx, e["title"], limit=25)
                        pool.extend(batch)  # one search often surfaces several episodes
                        hit = _pick(batch, e, series_q)
                    except Exception as exc:
                        log.warning("Second-chance search for '%s' failed: %s", e["title"], exc)
                if hit:
                    hit = dict(hit)
                    hit["_arr_se"] = (season, e["episode"])
                    _persist_arr_se(hit, season, e["episode"])
                    out.append(hit)
                else:
                    log.info("Season S%s: no item for E%02d '%s'", season, e["episode"], e["title"])
            log.info("Season S%s: matched %d/%d episodes (%d extra searches)",
                     season, len(out), len(eps), extra)
            return out
        log.info("Season-only request but no arr instance resolved the episode list")
    # non-arr schemes: filter by parsed season / absolute ordering
    out = []
    for it in items:
        if idx["naming"] == "absolute" and it.get("ordinal") and season == 1:
            it = dict(it)
            it["_arr_se"] = (1, it["ordinal"])
            out.append(it)
        else:
            se = naming.parse_sxxeyy(it["title"])
            if se and se[0] == season:
                out.append(it)
    return out


def _persist_arr_se(hit, season, ep):
    meta = hit.get("meta") or {}
    if isinstance(meta, str):
        meta = json.loads(meta or "{}")
    meta["arr_se"] = [season, ep]
    hit["meta"] = meta
    db.cache_upsert([hit])


def _arr_title_match(idx, items, series_q, season, ep):
    """Resolve the requested episode via the upstream arr and match our items by TITLE.

    TVDB maps YouTube channels to year-seasons (S2026E17); upload order and dates can't
    produce those numbers, but the arr knows the episode's title — so ask it. If the title
    isn't among the series-search results (older videos fall off search relevance), a
    second-chance search for the episode title itself is run."""
    for inst in config.get()["instances"]:
        if not inst.get("enabled") or inst["type"] not in ("sonarr", "whisparr"):
            continue
        try:
            info = arr.find_episode(inst, series_q, season, ep)
        except Exception as exc:
            log.warning("Arr lookup on %s failed: %s", inst["name"], exc)
            continue
        if not info:
            continue
        hit = _pick(items, info, series_q)
        if not hit:
            log.info("Episode title '%s' not in the %d series-search results — "
                     "second-chance search for the title itself", info["title"], len(items))
            try:
                extra = indexers.search(idx, info["title"], limit=25)
                hit = _pick(extra, info, series_q)
            except Exception as exc:
                log.warning("Second-chance search failed: %s", exc)
        if hit:
            hit["_arr_se"] = (season, ep)
            _persist_arr_se(hit, season, ep)  # the NZB/job must carry the same name
            log.info("Title match via %s: '%s' == S%02dE%03d '%s'",
                     inst["name"], hit["title"], season, ep, info["title"])
            return hit
        log.info("Arr episode '%s' (air %s) found on %s but no item matches even after the "
                 "second-chance search", info["title"], info.get("airDate"), inst["name"])
    return None


def _pick(items, info, series_q=""):
    series = info.get("series") or series_q
    title_hits = [it for it in items if arr._title_match(it["title"], info["title"])]
    if not title_hits:
        return None
    for it in title_hits:
        if arr._title_match(it.get("series_title") or "", series):
            return it
    if len(title_hits) == 1:
        log.warning("Title '%s' only matches an item from channel '%s' (expected series '%s') "
                    "— accepting the single candidate", info["title"],
                    title_hits[0].get("series_title"), series)
        return title_hits[0]
    log.warning("Title '%s' matches %d items but none from series '%s' (channels: %s) — "
                "rejecting to avoid a wrong grab", info["title"], len(title_hits), series,
                ", ".join(sorted({t.get("series_title") or "?" for t in title_hits})))
    return None
