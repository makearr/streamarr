import os

from streamarr import config, db, naming
from tests.test_arr_configure import mock_arr  # noqa: F401


def test_clean_keeps_fraction_readable():
    assert naming.clean("Essen (1/3) Test") == "Essen (1-3) Test"
    assert "/" not in naming.clean("a/b\\c")


def test_arr_title_strips_embedded_numbering():
    t = naming.release_title({"series_title": "Terra X", "_arr_se": (2015, 14),
                              "title": "Die Geschichte (2/3) - Rach (S01/E02)",
                              "published": 1428000000}, "arr")
    assert "S2015E014" in t and "S01E02" not in t and "S01-E02" not in t
    assert "(2-3)" in t


def test_quick_add_guessing(logged_in):
    r = logged_in.post("/ui/subs/quick", json={"url": "https://www.pornhub.com/model/bluecrow42"})
    g = r.json()
    assert g["site_preset"] == "pornhub" and g["title"] == "bluecrow42"
    assert g["path"].endswith(os.path.join("pornhub", "bluecrow42"))
    r2 = logged_in.post("/ui/subs/quick", json={"url": "https://www.youtube.com/@HybridCalisthenics",
                                                "backlog": True})
    g2 = r2.json()
    assert g2["provider"] == "youtube" and g2["title"] == "HybridCalisthenics"
    assert g2["path"].endswith(os.path.join("youtube", "HybridCalisthenics"))
    assert g2["initial"] == "backlog"
    assert logged_in.post("/ui/subs/quick", json={"url": "notaurl"}).status_code == 400
    logged_in.post("/ui/subs", json=[])


def test_backlog_mode_downloads_existing(monkeypatch, logged_in, api_key):
    from streamarr import subscriptions
    logged_in.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    items = [{"id": "youtube:backlog00001", "indexer_id": "sub_bl1", "provider": "youtube",
              "series_title": "BL", "title": "Old video", "url": "https://example.invalid/o",
              "published": 1700000000, "duration": 5, "ordinal": 1, "meta": {"exact_date": True}}]
    sub = dict(config.SUB_DEFAULTS, id="bl1", title="BL", url="https://example.invalid/c",
               provider="youtube", initial="backlog", priority=2)
    monkeypatch.setitem(config.get(), "subs", [sub])
    monkeypatch.setitem(config.get(), "indexers", [])
    monkeypatch.setattr(subscriptions, "_list", lambda i, s: items)
    monkeypatch.setattr(subscriptions, "_arr_already_has", lambda t: None)
    assert subscriptions.process(force=True) == 1  # backlog downloads on the FIRST check
    j = next(x for x in db.jobs_active() if "Old video" in x["name"])
    assert j["priority"] == 2  # per-subscription priority applied
    db.job_delete(j["nzo_id"])


def test_addfile_priority_and_audio_from_instance(mock_arr, logged_in, api_key):
    from tests.test_arr_configure import _inst
    inst = _inst()
    inst["default_priority"] = 1
    config.update("instances", [inst])
    db.cache_upsert([{"id": "youtube:priotest0001", "indexer_id": "yt", "provider": "youtube",
                      "series_title": "P", "title": "Prio", "url": "https://example.invalid/p",
                      "published": 1752000000, "duration": 5, "ordinal": 1,
                      "meta": {"exact_date": True}}])
    logged_in.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    try:
        nzb = logged_in.get(f"/newznab/yt/download/youtube:priotest0001.nzb?apikey={api_key}").text
        r = logged_in.post(f"/sabnzbd/api?mode=addfile&cat=tv&apikey={api_key}",
                           files={"nzbfile": ("p.nzb", nzb, "application/x-nzb")})
        nzo = r.json()["nzo_ids"][0]
        j = db.job_get(nzo)
        assert j["priority"] == 1  # sonarr default_priority applied via category tv
        db.job_delete(nzo)
        # lidarr-style push: music category forces audio media
        r = logged_in.post(f"/sabnzbd/api?mode=addfile&cat=music&apikey={api_key}",
                           files={"nzbfile": ("p.nzb", nzb, "application/x-nzb")})
        j = db.job_get(r.json()["nzo_ids"][0])
        assert j["media"] == "audio"
        db.job_delete(j["nzo_id"])
    finally:
        config.update("instances", [])


def test_lidarr_artist_param_search(client, api_key):
    db.cache_upsert([{"id": "youtube:artist000001", "indexer_id": "ytm", "provider": "youtube",
                      "series_title": "Some Artist", "title": "Some Artist - Great Song",
                      "url": "https://example.invalid/a", "published": 1752000000,
                      "duration": 200, "ordinal": None, "meta": {"exact_date": True}}])
    r = client.get(f"/newznab/ytm/api?t=music&artist=Some%20Artist&apikey={api_key}")
    assert "Great Song" in r.text


def test_stats_timeseries(logged_in):
    db.stat("yt", "grab", "x")
    db.stat("total", "speed", "2000000")
    d = logged_in.get("/ui/stats/timeseries?range=24h").json()
    assert len(d["grabs"]) == 48 and len(d["speed"]) == 48
    assert sum(d["grabs"]) >= 1 and max(d["speed"]) >= 2000000


def test_new_presets_present(client):
    from streamarr.providers.site import SITE_PRESETS
    for p in ("ytmusic", "bandcamp", "mixcloud", "audiomack", "redtube", "spankbang",
              "eporner", "tnaflix", "twitch", "dailymotion", "rumble", "bilibili",
              "nicovideo", "twitter", "instagram", "reddit", "archiveorg", "odysee", "bitchute"):
        assert p in SITE_PRESETS
    audio = [p for p in SITE_PRESETS.values() if p.get("media") == "audio"]
    adult = [p for p in SITE_PRESETS.values() if p.get("adult")]
    assert len(audio) >= 4 and len(adult) >= 6


def test_port_setting(logged_in):
    old = config.get()["streamarr"]["port"]
    try:
        r = logged_in.post("/ui/settings/streamarr", json={"port": 9090})
        assert r.status_code == 200
        assert config.get()["streamarr"]["port"] == 9090
        assert logged_in.post("/ui/settings/streamarr", json={"port": "bad"}).status_code == 400
        assert logged_in.post("/ui/settings/streamarr", json={"port": 99999}).status_code == 400
    finally:
        config.update("streamarr", {"port": old})


def test_downloads_root_dedup(logged_in):
    from streamarr import config
    from streamarr.api import ui
    old = config.get()["downloads"]["path"]
    try:
        config.get()["downloads"]["path"] = "/downloads/youtube"
        assert ui._downloads_root() == "/downloads"
        g = ui._guess_sub("https://www.pornhub.com/model/bluecrow42")
        assert g["path"] == "/downloads/pornhub/bluecrow42"
        config.get()["downloads"]["path"] = "/downloads"
        assert ui._downloads_root() == "/downloads"
    finally:
        config.get()["downloads"]["path"] = old


def test_sub_run_one(monkeypatch, logged_in):
    from streamarr import config, subscriptions
    sub = dict(config.SUB_DEFAULTS, id="runone", title="RunOne", url="https://example.invalid/c",
               provider="youtube")
    config.update("subs", [sub])
    called = {}
    monkeypatch.setattr(subscriptions, "process_one", lambda sid, **k: called.setdefault("id", sid))
    try:
        assert logged_in.post("/ui/subs/run", json={"id": "runone"}).status_code == 200
        assert logged_in.post("/ui/subs/run", json={"id": "nope"}).status_code == 404
    finally:
        config.update("subs", [])


def test_impersonation_probe_is_safe():
    from streamarr import downloader
    opts = {}
    downloader._apply_impersonation(opts, {"impersonate": True})  # must not raise
    downloader._apply_impersonation(opts, {"impersonate": False})
    assert "impersonate" not in opts or opts.get("impersonate")


def test_setup_requires_session_when_account_exists(client, logged_in):
    """Account takeover guard: with forms login active, an unauthenticated request must not
    be able to replace the credentials."""
    from streamarr import config
    assert config.get()["streamarr"]["auth"].get("password_hash")
    old_mode = config.get()["streamarr"]["auth"]["mode"]
    a = dict(config.get()["streamarr"]["auth"], mode="forms")
    config.update("streamarr", {"auth": a})
    try:
        from fastapi.testclient import TestClient
        from streamarr.main import app
        anon = TestClient(app)  # fresh client: no session cookie
        r = anon.post("/ui/auth/setup", json={"username": "attacker", "password": "pwned"})
        assert r.status_code == 403
        assert config.get()["streamarr"]["auth"]["username"] != "attacker"
        # a logged-in session may still change the account
        r = logged_in.post("/ui/auth/setup", json={"username": "tester", "password": "testpass123"})
        assert r.status_code == 200
    finally:
        a = dict(config.get()["streamarr"]["auth"], mode=old_mode)
        config.update("streamarr", {"auth": a})


def test_gzip_and_indexes(client):
    r = client.get("/ui/presets", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip" or len(r.content) < 1024
    from streamarr import db
    idx = [r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='index'")]
    assert "idx_cache_indexer_pub" in idx and "idx_jobs_active" in idx and "idx_stats_ts" in idx


def test_start_now_button_labeled():
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "streamarr", "static", "app.js")
    src = open(path).read()
    assert '">Start now</button>' in src  # closing quote present — the label must render
