import logging
import time
from xml.sax.saxutils import escape

from . import config, db, naming
from .providers import ardzdf, mediathek, site, youtube
from .runtime import set_status, clear_status

log = logging.getLogger("streamarr.indexers")


def get_indexer(indexer_id):
    for idx in config.get()["indexers"]:
        if idx["id"] == indexer_id:
            return idx
    return None


def refresh_indexer(idx, force=False):
    """Refresh cached source listings for channel/source-mode indexers (youtube + site)."""
    if idx["provider"] not in ("youtube", "site") or not idx.get("channels"):
        return 0
    max_age = config.get()["cache"]["refresh_minutes"] * 60
    total = 0
    for ch in idx["channels"]:
        if not force and time.time() - db.cache_age(idx["id"], ch["title"]) < max_age:
            continue
        set_status(f"Refreshing source: {ch['title']}")
        try:
            if idx["provider"] == "youtube":
                items = youtube.list_channel(ch["url"], ch["title"], idx["id"])
            else:
                items = site.list_source(ch["url"], ch["title"], idx)
            db.cache_upsert(items)
            total += len(items)
            log.info("Cached %d entries for source '%s'", len(items), ch["title"])
        except Exception:
            log.warning("Refresh failed for source '%s' — serving stale cache", ch["title"])
    clear_status()
    return total


def search(idx, text, limit=100, offset=0):
    """Search one indexer. Returns cache item dicts. Also records stats."""
    db.stat(idx["id"], "search", text or "")
    provider = idx["provider"]
    if provider in ("mediathek", "ard", "zdf"):
        label = {"mediathek": "Mediathek", "ard": "ARD", "zdf": "ZDF"}[provider]
        set_status(f"Searching {label}: {text or 'latest'}")
        try:
            if provider == "mediathek":
                items = mediathek.search(text, idx["id"], limit=limit, offset=offset)
            else:
                items = ardzdf.search(provider, text, idx, limit=limit)
            db.cache_upsert(items)
        finally:
            clear_status()
    else:  # youtube | site: cached sources + optional live search
        refresh_indexer(idx)
        items = db.cache_search(idx["id"], text, limit=limit, offset=offset)
        if not items and text:
            live = None
            if provider == "youtube" and idx.get("broad_search"):
                live = ("YouTube broad search", lambda: youtube.broad_search(text, idx["id"], limit=min(limit, 25)))
            elif provider == "site" and (idx.get("search_template") or site.preset(idx)["search_template"]):
                live = (f"{site.preset(idx)['name']} search", lambda: site.search(idx, text, limit=min(limit, 25)))
            if live:
                set_status(f"{live[0]}: {text}")
                try:
                    items = live[1]()
                    db.cache_upsert(items)
                finally:
                    clear_status()
    if idx["naming"] == "sxxeyy":  # strict: only items carrying a parseable SxxEyy tag
        before = len(items)
        items = [it for it in items if naming.parse_sxxeyy(it["title"])]
        if before != len(items):
            log.info("sxxeyy strict filter: %d -> %d items", before, len(items))
    log.info("Search '%s' on indexer '%s' (%s): %d result(s)", text or "<all>",
             idx["id"], provider, len(items))
    return items


def newznab_caps(idx, base):
    cats = "".join(
        f'<category id="{c}" name="{_cat_name(c)}"/>' for c in idx["categories"])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server appversion="1.0" version="1.0" title="Streamarr — {escape(idx['name'])}" url="{escape(base)}"/>
  <limits max="100" default="100"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep"/>
    <movie-search available="yes" supportedParams="q"/>
    <audio-search available="yes" supportedParams="q"/>
  </searching>
  <categories>{cats}</categories>
</caps>"""


def _expand_cats(cats):
    """Parent categories plus their standard subcategories (5000 -> 5040 HD etc.) so arr
    apps configured with subcategory defaults still see matching releases."""
    out = []
    for c in cats:
        c = int(c)
        out.append(c)
        if c in (2000, 5000):
            out.append(c + 40)
        elif c == 3000:
            out.append(3010)
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _cat_name(c):
    return {2000: "Movies", 3000: "Audio", 5000: "TV", 6000: "XXX"}.get(int(c), str(c))


def newznab_results(idx, items, base, api_key):
    tag = naming.quality_tag(config.quality_for(idx), idx["media"])
    entries = []
    for it in items:
        title = naming.release_title(it, idx["naming"], tag)
        guid = it["id"]
        link = f"{base}/newznab/{idx['id']}/download/{guid}.nzb?apikey={api_key}"
        size = (it.get("duration") or 1800) * (250_000 if idx["media"] == "video" else 20_000)
        pub = time.strftime("%a, %d %b %Y %H:%M:%S +0000",
                            time.gmtime(it.get("published") or time.time()))
        allcats = _expand_cats(idx["categories"])
        cat_elems = "".join(f"<category>{c}</category>" for c in allcats)
        cat_attrs = "".join(f'<newznab:attr name="category" value="{c}"/>' for c in allcats)
        entries.append(f"""<item>
  <title>{escape(title)}</title>
  <guid isPermaLink="false">{escape(guid)}</guid>
  <link>{escape(link)}</link>
  <pubDate>{pub}</pubDate>
  {cat_elems}
  <enclosure url="{escape(link)}" length="{size}" type="application/x-nzb"/>
  {cat_attrs}
  <newznab:attr name="size" value="{size}"/>
</item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
<channel>
  <title>Streamarr — {escape(idx['name'])}</title>
  <description>Streamarr Newznab feed</description>
  {''.join(entries)}
</channel>
</rss>"""
