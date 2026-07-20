from streamarr import db


def _seed(iid, title, ordinal=None):
    item = {"id": f"youtube:{iid}", "indexer_id": "yta" if ordinal else "yts",
            "provider": "youtube", "series_title": "Serie", "title": title,
            "url": "https://example.invalid/v", "published": 1752000000,
            "duration": 600, "ordinal": ordinal, "meta": {}}
    db.cache_upsert([item])
    return item


def test_arr_naming_takes_request_episode(client, api_key):
    _seed("arritem0001", "Some upload title", ordinal=5)
    r = client.get(f"/newznab/yta/api?t=tvsearch&q=Some&season=1&ep=5&apikey={api_key}")
    assert "S01E005" in r.text


def test_sxxeyy_strict_drops_untagged(client, api_key):
    db.cache_upsert([
        {"id": "youtube:tag00000001", "indexer_id": "yts", "provider": "youtube",
         "series_title": "Serie", "title": "S02E03 Folge mit Tag", "url": "https://example.invalid/1",
         "published": 1752000000, "duration": 1, "ordinal": None, "meta": {}},
        {"id": "youtube:notag000001", "indexer_id": "yts", "provider": "youtube",
         "series_title": "Serie", "title": "Ohne Nummer", "url": "https://example.invalid/2",
         "published": 1752000000, "duration": 1, "ordinal": None, "meta": {}},
    ])
    r = client.get(f"/newznab/yts/api?t=tvsearch&apikey={api_key}")
    assert "S02E003" in r.text and "Ohne Nummer" not in r.text


def test_site_preset_search_template():
    from streamarr.providers.site import SITE_PRESETS
    assert "{query}" in SITE_PRESETS["pornhub"]["search_template"]
    assert SITE_PRESETS["soundcloud"]["search_template"].startswith("scsearch")
    assert set(SITE_PRESETS) >= {"bbc", "abcnews", "adultswim", "cnn", "facebook", "pornhub",
                                 "xhamster", "lastfm", "peertube", "soundcloud", "ted",
                                 "tiktok", "vimeo", "xvideos", "youporn", "custom"}
