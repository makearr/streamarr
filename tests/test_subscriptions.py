from streamarr import config, db, subscriptions


def test_sab_api_alias_root(client, api_key):
    """Sonarr calls {host}/{urlBase}/api — with empty urlBase that's /api, which must answer JSON."""
    r = client.get(f"/api?mode=version&apikey={api_key}&output=json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["version"].startswith("1.")


ITEMS_V1 = [
    {"id": "youtube:sub00000001", "indexer_id": "yt", "provider": "youtube", "series_title": "SubChan",
     "title": "Old video", "url": "https://example.invalid/1", "published": 1752000000,
     "duration": 60, "ordinal": 1, "meta": {}},
]
ITEMS_V2 = ITEMS_V1 + [
    {"id": "youtube:sub00000002", "indexer_id": "yt", "provider": "youtube", "series_title": "SubChan",
     "title": "New video", "url": "https://example.invalid/2", "published": 1752100000,
     "duration": 60, "ordinal": 2, "meta": {}},
]


def _sub_indexer():
    idx = dict(config.INDEXER_DEFAULTS)
    idx.update({"id": "yt", "name": "YT", "provider": "youtube",
                "subscriptions": [{"title": "SubChan", "url": "https://example.invalid/c"}]})
    return idx


def test_subscription_baseline_then_new(monkeypatch, logged_in, api_key):
    listing = {"items": ITEMS_V1}
    monkeypatch.setattr(subscriptions, "_list", lambda idx, sub: listing["items"])
    monkeypatch.setattr(config, "get", config.get)  # noop, clarity
    idx = _sub_indexer()
    monkeypatch.setitem(config.get(), "indexers", [idx])
    logged_in.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")

    assert subscriptions.process(force=True) == 0          # baseline: nothing downloaded
    listing["items"] = ITEMS_V2
    assert subscriptions.process(force=True) == 1          # only the new item
    jobs = db.jobs_active()
    assert any("New video" in j["name"] for j in jobs)
    assert not any("Old video" in j["name"] for j in jobs)
    assert subscriptions.process(force=True) == 0          # idempotent
    for j in db.jobs_active():
        if "New video" in j["name"]:
            db.job_delete(j["nzo_id"])


def test_subscription_arr_crosscheck_skips(monkeypatch, logged_in, api_key):
    items = [dict(ITEMS_V2[1], id="youtube:sub00000003", title="Another new")]
    db.subs_mark("youtube:baseline", "yt", "SubChan2", downloaded=False)  # source not new
    idx = _sub_indexer()
    idx["subscriptions"] = [{"title": "SubChan2", "url": "https://example.invalid/c2"}]
    monkeypatch.setitem(config.get(), "indexers", [idx])
    monkeypatch.setattr(subscriptions, "_list", lambda i, s: items)
    monkeypatch.setattr(subscriptions, "_arr_already_has", lambda title: "Sonarr")
    config.update("subscriptions", {"check_arr": True})
    before = len(db.jobs_active())
    assert subscriptions.process(force=True) == 0          # skipped: arr already has it
    assert len(db.jobs_active()) == before


def test_category_mapping():
    assert subscriptions.category_for({"categories": [5000]}) == "tv"
    assert subscriptions.category_for({"categories": [3030]}) == "audiobooks"
    assert subscriptions.category_for({"categories": [6000]}) == "adult"
