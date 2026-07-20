import logging
import time
import urllib.parse

from . import youtube

log = logging.getLogger("streamarr.site")

# Presets: search_template uses {query}; None = source-list mode only.
# scsearch is yt-dlp's native SoundCloud search prefix.
SITE_PRESETS = {
    "bbc": {"name": "BBC iPlayer", "search_template": None, "categories": [5000, 2000],
            "hint": "Add programme/brand URLs (e.g. https://www.bbc.co.uk/iplayer/episodes/<id>) as sources — like iplayarr's programme list."},
    "abcnews": {"name": "ABC News", "search_template": None, "categories": [5000],
                "hint": "Add show/section URLs from abcnews.go.com as sources."},
    "adultswim": {"name": "Adult Swim", "search_template": None, "categories": [5000],
                  "hint": "Add show URLs (https://www.adultswim.com/videos/<show>) as sources."},
    "cnn": {"name": "CNN", "search_template": None, "categories": [5000],
            "hint": "Add section/show URLs from cnn.com as sources."},
    "facebook": {"name": "Facebook", "search_template": None, "categories": [5000],
                 "hint": "Add page video-tab URLs (https://www.facebook.com/<page>/videos) as sources."},
    "pornhub": {"name": "Pornhub", "search_template": "https://www.pornhub.com/video/search?search={query}",
                "categories": [6000], "adult": True,
                "hint": "Search works directly; model/channel URLs can be added as sources."},
    "xhamster": {"name": "xHamster", "search_template": "https://xhamster.com/search/{query}",
                 "categories": [6000], "adult": True,
                 "hint": "Search works directly; creator URLs can be added as sources."},
    "lastfm": {"name": "last.fm", "search_template": None, "categories": [3000], "media": "audio",
               "hint": "Add last.fm playlist URLs as sources (resolved via YouTube)."},
    "peertube": {"name": "PeerTube", "search_template": None, "categories": [5000],
                 "hint": "Add channel/account URLs of your PeerTube instance as sources."},
    "soundcloud": {"name": "SoundCloud", "search_template": "scsearch25:{query}",
                   "categories": [3000], "media": "audio",
                   "hint": "Search works directly; artist/set URLs can be added as sources."},
    "ted": {"name": "TED Talks", "search_template": None, "categories": [5000],
            "hint": "Add speaker/playlist URLs from ted.com as sources."},
    "tiktok": {"name": "TikTok", "search_template": None, "categories": [5000],
               "hint": "Add profile URLs (https://www.tiktok.com/@user) as sources."},
    "vimeo": {"name": "Vimeo", "search_template": None, "categories": [5000],
              "hint": "Add channel/user/showcase URLs as sources."},
    "xvideos": {"name": "XVideos", "search_template": "https://www.xvideos.com/?k={query}",
                "categories": [6000], "adult": True,
                "hint": "Search works directly; channel URLs can be added as sources."},
    "youporn": {"name": "YouPorn", "search_template": "https://www.youporn.com/search/?query={query}",
                "categories": [6000], "adult": True,
                "hint": "Search works directly; channel URLs can be added as sources."},
    "podcast": {"name": "Podcasts", "search_template": None, "categories": [3000, 3010],
                "media": "audio", "audio_format": "mp3",
                "hint": "Audio-only (MP3). Add podcast show pages yt-dlp can list (YouTube podcast playlists, SoundCloud shows, platform pages) as sources."},
    "audiobook": {"name": "Audiobooks", "search_template": None, "categories": [3030, 7000],
                  "media": "audio", "audio_format": "m4b",
                  "hint": "Audio-only (M4B). Add audiobook channels/playlists (e.g. LibriVox on YouTube/archive.org) as sources — pairs with Readarr."},
    "ytmusic": {"name": "YouTube Music", "search_template": "https://music.youtube.com/search?q={query}",
                "categories": [3000], "media": "audio",
                "hint": "Artist/playlist URLs from music.youtube.com as sources; searches resolve via YouTube. Ideal for Lidarr."},
    "bandcamp": {"name": "Bandcamp", "search_template": None, "categories": [3000], "media": "audio",
                 "hint": "Add artist or album URLs (https://<artist>.bandcamp.com/music) as sources."},
    "mixcloud": {"name": "Mixcloud", "search_template": None, "categories": [3000], "media": "audio",
                 "hint": "Add creator URLs (https://www.mixcloud.com/<user>/) as sources."},
    "audiomack": {"name": "Audiomack", "search_template": None, "categories": [3000], "media": "audio",
                  "hint": "Add artist URLs (https://audiomack.com/<artist>) as sources."},
    "redtube": {"name": "RedTube", "search_template": "https://www.redtube.com/?search={query}",
                "categories": [6000], "adult": True,
                "hint": "Search works directly; channel URLs can be added as sources."},
    "spankbang": {"name": "SpankBang", "search_template": "https://spankbang.com/s/{query}/",
                  "categories": [6000], "adult": True,
                  "hint": "Search works directly; model/channel URLs can be added as sources."},
    "eporner": {"name": "Eporner", "search_template": "https://www.eporner.com/search/{query}/",
                "categories": [6000], "adult": True,
                "hint": "Search works directly; profile URLs can be added as sources."},
    "tnaflix": {"name": "TNAFlix", "search_template": None, "categories": [6000], "adult": True,
                "hint": "Add channel/profile URLs as sources."},
    "twitch": {"name": "Twitch (VODs)", "search_template": None, "categories": [5000],
               "hint": "Add channel video URLs (https://www.twitch.tv/<channel>/videos) as sources. Live streams are not downloaded."},
    "dailymotion": {"name": "Dailymotion", "search_template": None, "categories": [5000],
                    "hint": "Add channel URLs (https://www.dailymotion.com/<channel>) as sources."},
    "rumble": {"name": "Rumble", "search_template": None, "categories": [5000],
               "hint": "Add channel URLs (https://rumble.com/c/<channel>) as sources."},
    "bilibili": {"name": "Bilibili", "search_template": None, "categories": [5000],
                 "hint": "Add space/channel URLs (https://space.bilibili.com/<id>) as sources."},
    "nicovideo": {"name": "Niconico", "search_template": None, "categories": [5000],
                  "hint": "Add user/channel URLs (https://www.nicovideo.jp/user/<id>/video) as sources. May require credentials via a proxy in some regions."},
    "twitter": {"name": "X / Twitter", "search_template": None, "categories": [5000],
                "hint": "Add profile media URLs (https://x.com/<user>/media) as sources."},
    "instagram": {"name": "Instagram", "search_template": None, "categories": [5000],
                  "hint": "Add profile URLs as sources. Instagram heavily rate-limits anonymous access."},
    "reddit": {"name": "Reddit", "search_template": None, "categories": [5000],
               "hint": "Add subreddit URLs (https://www.reddit.com/r/<sub>/) as sources; only native video posts download."},
    "archiveorg": {"name": "Internet Archive", "search_template": None, "categories": [5000, 2000],
                   "hint": "Add collection or item URLs (https://archive.org/details/<id>) as sources."},
    "odysee": {"name": "Odysee", "search_template": None, "categories": [5000],
               "hint": "Add channel URLs (https://odysee.com/@<channel>) as sources."},
    "bitchute": {"name": "BitChute", "search_template": None, "categories": [5000],
                 "hint": "Add channel URLs (https://www.bitchute.com/channel/<id>/) as sources."},
    "custom": {"name": "Custom site (yt-dlp)", "search_template": None, "categories": [5000],
               "hint": "Any yt-dlp-supported site: add listable URLs as sources and/or set a search URL template with {query}."},
}


def preset(idx):
    return SITE_PRESETS.get(idx.get("site_preset") or "custom", SITE_PRESETS["custom"])


def _limiter_key(idx):
    return idx.get("site_preset") or "site"


def _to_items(entries, idx, series_title=None, with_ordinal=False):
    items = []
    for i, e in enumerate(entries, start=1):
        if not e:
            continue
        url = e.get("url") or e.get("webpage_url")
        eid = e.get("id") or url
        if not url or not eid:
            continue
        items.append({
            "id": f"{_limiter_key(idx)}:{eid}",
            "indexer_id": idx["id"],
            "provider": "site",
            "series_title": series_title or e.get("channel") or e.get("uploader") or preset(idx)["name"],
            "title": e.get("title") or str(eid),
            "url": url,
            "published": int(e.get("timestamp") or 0) or (None if with_ordinal else int(time.time())),
            "duration": int(e.get("duration") or 0) or None,
            "ordinal": i if with_ordinal else None,
            "meta": {"channel": e.get("channel") or e.get("uploader"), "preset": idx.get("site_preset")},
        })
    return items


def list_source(source_url, series_title, idx, limit=500):
    """Enumerate one source URL (channel/show/playlist) — ordinal = listing order, oldest first."""
    info = youtube._extract(source_url, {"playlistend": limit},
                            what=f"{preset(idx)['name']} source {series_title}",
                            provider=_limiter_key(idx))
    entries = [e for e in (info.get("entries") or []) if e]
    entries.reverse()
    return _to_items(entries, idx, series_title=series_title, with_ordinal=True)


def search(idx, text, limit=25):
    template = idx.get("search_template") or preset(idx)["search_template"]
    if not template:
        return []
    if template.startswith("scsearch"):
        target = template.format(query=text)
    else:
        target = template.format(query=urllib.parse.quote_plus(text))
    info = youtube._extract(target, {"playlistend": limit},
                            what=f"{preset(idx)['name']} search", provider=_limiter_key(idx))
    entries = (info.get("entries") or [])[:limit]
    return _to_items(entries, idx)
