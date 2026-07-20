import logging
import time

import yt_dlp

from .. import config
from ..runtime import limiter, log_connection_error

log = logging.getLogger("streamarr.youtube")


def _ydl_opts(extra=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 20,
        # flat listings normally carry no dates; this yields (approximate) upload timestamps
        "extractor_args": {"youtubetab": {"approximate_date": ["timestamp"]}},
    }
    opts.update(extra or {})
    return opts


def _is_rate_limit(exc):
    text = str(exc).lower()
    return any(s in text for s in ("429", "too many requests", "rate limit", "throttl", "503"))


def _extract(url, extra=None, what="YouTube", provider="youtube"):
    limiter.wait(provider)
    opts = _ydl_opts(extra)
    proxy = config.proxy_for(url)
    if proxy:
        opts["proxy"] = proxy
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        limiter.reset(provider)
        info = info or {}
        n = len(info.get("entries") or []) if "entries" in info else 1
        log.info("%s: extracted %d entr%s from %s", what, n, "y" if n == 1 else "ies", url)
        return info
    except Exception as exc:
        if _is_rate_limit(exc):
            limiter.penalize(provider)
        log_connection_error(log, what, url, exc)
        raise


def list_channel(channel_url, series_title, indexer_id, limit=500):
    """Return cache item dicts for a channel, oldest first for stable ordinals."""
    info = _extract(f"{channel_url.rstrip('/')}/videos", {"playlistend": limit},
                    what=f"YouTube channel {series_title}")
    entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
    entries.reverse()  # yt-dlp lists newest first; ordinal = upload order
    items = []
    for i, e in enumerate(entries, start=1):
        items.append({
            "id": f"youtube:{e['id']}",
            "indexer_id": indexer_id,
            "provider": "youtube",
            "series_title": series_title,
            "title": e.get("title") or e["id"],
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
            "published": int(e.get("timestamp") or 0) or None,  # approximate upload date
            "duration": int(e.get("duration") or 0) or None,
            "ordinal": i,
            "meta": {"channel": series_title},
        })
    return items


def broad_search(text, indexer_id, limit=25):
    info = _extract(f"ytsearch{limit}:{text}", what="YouTube search")
    items = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        items.append({
            "id": f"youtube:{e['id']}",
            "indexer_id": indexer_id,
            "provider": "youtube",
            "series_title": e.get("channel") or e.get("uploader") or "YouTube",
            "title": e.get("title") or e["id"],
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
            "published": int(e.get("timestamp") or 0) or int(time.time()),
            "duration": int(e.get("duration") or 0) or None,
            "ordinal": None,
            "meta": {"channel": e.get("channel") or e.get("uploader")},
        })
    return items


def format_opts(quality, media):
    """(format, format_sort) — sort-based selection instead of hard filters.

    Hard filters like bestvideo[ext=mp4] cap YouTube at 480p whenever HD is only served
    as vp9/av01-webm (very common): the filtered selector SUCCEEDS with the best mp4 —
    480p — so later fallbacks never run. format_sort expresses preferences without
    excluding formats, so the resolution cap always wins.
    """
    if media == "audio":
        ext = quality["audio_format"]
        sort_ext = "m4a" if ext == "m4b" else ext
        return "ba/b", [f"aext:{sort_ext}", "abr"]
    h, fps, ext = quality["max_resolution"], quality["max_fps"], quality["video_format"]
    return "bv*+ba/b", [f"res:{h}", f"fps:{fps}", f"vext:{ext}", "aext:m4a"]


def format_string(quality, media):
    """Build a yt-dlp format selector from the quality config (harvestarr-style fallbacks)."""
    if media == "audio":
        codec, ext = quality["audio_codec"], quality["audio_format"]
        return f"bestaudio[acodec={codec}]/bestaudio[ext={ext}]/bestaudio/best"
    h, fps, ext, codec = (quality["max_resolution"], quality["max_fps"],
                          quality["video_format"], quality["audio_codec"])
    v = f"bestvideo[height<={h}][fps<={fps}][ext={ext}]"
    return (f"{v}+bestaudio[acodec={codec}]/"
            f"{v}+bestaudio[ext=m4a]/"
            f"bestvideo[height<={h}][fps<={fps}]+bestaudio/"
            f"best[height<={h}][fps<={fps}]/"
            f"best")  # direct-URL sources (Mediathek etc.) carry no format metadata


def video_details(url):
    """Exact metadata for one video (single request): (timestamp, duration)."""
    info = _extract(url, {"extract_flat": False, "skip_download": True},
                    what="YouTube video details")
    ts = info.get("timestamp")
    if not ts and info.get("upload_date"):
        import datetime
        d = info["upload_date"]  # YYYYMMDD
        ts = int(datetime.datetime(int(d[:4]), int(d[4:6]), int(d[6:8]),
                                   tzinfo=datetime.timezone.utc).timestamp())
    return ts, int(info.get("duration") or 0) or None


def ensure_exact_date(item):
    """Replace an approximate/missing date with the video's real upload date (cached)."""
    import json as _json
    from .. import db
    meta = item.get("meta") or {}
    if isinstance(meta, str):
        meta = _json.loads(meta or "{}")
    if item.get("provider") != "youtube" or meta.get("exact_date"):
        item["meta"] = meta
        return item
    try:
        ts, duration = video_details(item["url"])
        if ts:
            item["published"] = ts
            log.info("Exact upload date for '%s': %s", item["title"],
                     __import__("datetime").datetime.utcfromtimestamp(ts).date())
        if duration:
            item["duration"] = duration
        meta["exact_date"] = True
        item["meta"] = meta
        db.cache_upsert([item])
    except Exception as exc:
        log.warning("Could not fetch exact date for '%s': %s", item["title"], exc)
        item["meta"] = meta
    return item
