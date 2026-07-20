def test_manual_search_uses_cache(logged_in, seeded_item):
    r = logged_in.get("/ui/search", params={"indexer_id": "yt", "q": "Optik"})
    assert r.status_code == 200
    assert any("S01E012" in i["release_title"] for i in r.json())


def test_manual_grab(logged_in, seeded_item, api_key):
    logged_in.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    r = logged_in.post("/ui/grab", json={"indexer_id": "yt", "item_id": seeded_item["id"], "category": "tv"})
    assert r.status_code == 200
    nzo = r.json()["nzo_id"]
    q = logged_in.get("/ui/queue").json()
    assert any(j["nzo_id"] == nzo for j in q["jobs"])
    logged_in.post("/ui/queue/delete", json={"nzo_id": nzo})


def test_indexer_validation(logged_in):
    r = logged_in.post("/ui/indexers", json=[{"id": "BAD ID!", "provider": "youtube"}])
    assert r.status_code == 400


def test_settings_roundtrip(logged_in):
    r = logged_in.post("/ui/settings/quality", json={"max_resolution": 720})
    assert r.status_code == 200 and r.json()["max_resolution"] == 720
    logged_in.post("/ui/settings/quality", json={"max_resolution": 1080})


def test_backup_download(logged_in):
    r = logged_in.get("/ui/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"


def test_stats_endpoint(logged_in):
    assert "summary" in logged_in.get("/ui/stats").json()


def test_logs_endpoint(logged_in):
    r = logged_in.get("/ui/logs")
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_manual_download(logged_in, api_key):
    logged_in.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    r = logged_in.post("/ui/download", json={"url": "https://example.invalid/clip", "name": "Clip", "category": "tv"})
    assert r.status_code == 200
    nzo = r.json()["nzo_id"]
    assert any(j["nzo_id"] == nzo for j in logged_in.get("/ui/queue").json()["jobs"])
    logged_in.post("/ui/queue/delete", json={"nzo_id": nzo})


def test_manual_download_requires_url(logged_in):
    assert logged_in.post("/ui/download", json={"url": ""}).status_code == 400


def test_presets_endpoint(logged_in):
    d = logged_in.get("/ui/presets").json()
    assert "ard" in d["providers"] and "zdf" in d["providers"] and "pornhub" in d["sites"]


def test_dashboard_endpoint(logged_in):
    d = logged_in.get("/ui/dashboard").json()
    assert {"queue", "indexers", "instances", "stats"} <= set(d)


def test_search_episode_filter(logged_in, seeded_item):
    r = logged_in.get("/ui/search", params={"indexer_id": "yt", "q": "Optik", "season": 1, "ep": 12})
    assert r.status_code == 200 and len(r.json()) >= 1
