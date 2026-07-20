import json
import logging

import httpx

from .. import config
from ..runtime import limiter, log_connection_error

log = logging.getLogger("streamarr.mediathek")
API = "https://mediathekviewweb.de/api/query"


def build_body(text, limit, offset):
    """Empty text must OMIT the queries clause — an empty-string query matches nothing,
    which made every arr indexer test ('recent releases', no q) come back empty."""
    body = {"sortBy": "timestamp", "sortOrder": "desc", "future": False,
            "size": limit, "offset": offset}
    if text:
        body["queries"] = [{"fields": ["title", "topic"], "query": text}]
    return body


def search(text, indexer_id, limit=50, offset=0):
    limiter.wait("mediathek")
    body = build_body(text, limit, offset)
    try:
        resp = httpx.post(API, content=json.dumps(body),
                          headers={"Content-Type": "text/plain"},
                          proxy=config.proxy_for(API), timeout=30)
        if resp.status_code in (429, 503):
            limiter.penalize("mediathek")
            resp.raise_for_status()
        resp.raise_for_status()
        limiter.reset("mediathek")
    except Exception as exc:
        log_connection_error(log, "MediathekViewWeb", API, exc)
        raise
    results = (resp.json().get("result") or {}).get("results") or []
    items = []
    for r in results:
        url = r.get("url_video_hd") or r.get("url_video") or r.get("url_video_low")
        if not url:
            continue
        vid = f"mediathek:{abs(hash((r.get('channel'), r.get('topic'), r.get('title'), r.get('timestamp'))))}"
        items.append({
            "id": vid,
            "indexer_id": indexer_id,
            "provider": "mediathek",
            "series_title": r.get("topic") or r.get("channel"),
            "title": r.get("title") or "",
            "url": url,
            "published": int(r.get("timestamp") or 0) or None,
            "duration": int(r.get("duration") or 0) or None,
            "ordinal": None,
            "meta": {"channel": r.get("channel"), "topic": r.get("topic")},
        })
    return items
