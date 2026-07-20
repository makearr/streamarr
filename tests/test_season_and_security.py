from streamarr import auth, config, db
from tests.test_arr_configure import _inst, mock_arr  # noqa: F401  (fixture reuse)


# ---------- season requests ----------

def test_season_request_expands_to_episodes(mock_arr, logged_in, api_key):
    db.cache_upsert([
        {"id": "youtube:verita000001", "indexer_id": "yta", "provider": "youtube",
         "series_title": "Veritasium", "title": "The Scariest Chart in Electrical Engineering",
         "url": "https://example.invalid/v1", "published": 1752537600, "duration": 1200,
         "ordinal": None, "meta": {"exact_date": True}},
        {"id": "youtube:verita000002", "indexer_id": "yta", "provider": "youtube",
         "series_title": "Veritasium", "title": "Why Bridges Don't Fall Down",
         "url": "https://example.invalid/v2", "published": 1752537601, "duration": 1100,
         "ordinal": None, "meta": {"exact_date": True}}])
    config.update("instances", [_inst()])
    try:
        r = logged_in.get(f"/newznab/yta/api?t=tvsearch&q=Veritasium&season=2026&apikey={api_key}")
        assert "S2026E017" in r.text and "S2026E018" in r.text
        assert r.text.count("<item>") == 2
    finally:
        config.update("instances", [])


def test_season_request_non_arr_scheme(client, api_key):
    db.cache_upsert([
        {"id": "youtube:sfilter00001", "indexer_id": "yts", "provider": "youtube",
         "series_title": "Serie", "title": "S03E01 Auftakt", "url": "https://example.invalid/a",
         "published": 1752000000, "duration": 1, "ordinal": None, "meta": {}},
        {"id": "youtube:sfilter00002", "indexer_id": "yts", "provider": "youtube",
         "series_title": "Serie", "title": "S04E01 Andere Staffel", "url": "https://example.invalid/b",
         "published": 1752000000, "duration": 1, "ordinal": None, "meta": {}}])
    r = client.get(f"/newznab/yts/api?t=tvsearch&q=Serie&season=3&apikey={api_key}")
    assert "S03E001" in r.text and "Andere Staffel" not in r.text


# ---------- secret transport ----------

def test_settings_masks_secrets(logged_in):
    logged_in.post("/ui/settings/proxy", json={"password": "supersecret"})
    s = logged_in.get("/ui/settings").json()
    assert "\u2026" in s["streamarr"]["api_key"] and len(s["streamarr"]["api_key"]) == 9
    assert s["proxy"]["password"] == "********"
    # sentinel round-trip keeps the stored secret
    logged_in.post("/ui/settings/proxy", json={"password": "********", "host": "p.example"})
    assert config.get()["proxy"]["password"] == "supersecret"
    logged_in.post("/ui/settings/proxy", json={"password": "", "host": "", "enabled": False})


def test_apikey_reveal_endpoint(logged_in):
    full = logged_in.post("/ui/apikey").json()["api_key"]
    assert full == config.get()["streamarr"]["api_key"] and len(full) == 32


def test_instances_mask_and_restore(mock_arr, logged_in):
    from tests.test_arr_configure import MOCK_KEY
    logged_in.post("/ui/instances", json=[_inst()])
    listed = logged_in.get("/ui/instances").json()
    assert listed[0]["api_key"] == "********"
    # saving the masked payload back must keep the real key working
    r = logged_in.post("/ui/instances", json=listed)
    assert r.json()[0]["api_key"] == "********"
    assert config.get()["instances"][0]["api_key"] == MOCK_KEY
    ok = logged_in.post("/ui/instances/test", json=listed[0]).json()
    assert ok["ok"] is True
    logged_in.post("/ui/instances", json=[])


def test_password_sha2_scheme_and_legacy_upgrade(logged_in):
    import hashlib
    # setup stores the sha2-prefixed scheme
    logged_in.post("/ui/auth/setup", json={"username": "tester", "password": "clienthash123"})
    a = config.get()["streamarr"]["auth"]
    assert a["password_hash"].startswith("sha2$")
    assert logged_in.get("/ui/auth/state").json()["pw_scheme"] == "sha2"
    r = logged_in.post("/ui/auth/login", json={"username": "tester", "password": "clienthash123"})
    assert r.status_code == 200
    # legacy hash: plaintext login succeeds once and upgrades the stored hash
    a2 = dict(a)
    a2["password_hash"] = auth.hash_password("oldplain")
    config.update("streamarr", {"auth": a2})
    assert logged_in.get("/ui/auth/state").json()["pw_scheme"] == "legacy"
    r = logged_in.post("/ui/auth/login", json={"username": "tester", "password": "oldplain"})
    assert r.status_code == 200
    upgraded = config.get()["streamarr"]["auth"]["password_hash"]
    assert upgraded.startswith("sha2$")
    digest = hashlib.sha256(b"oldplain").hexdigest()
    r = logged_in.post("/ui/auth/login", json={"username": "tester", "password": digest})
    assert r.status_code == 200
    # restore the account the rest of the suite expects
    logged_in.post("/ui/auth/setup", json={"username": "tester", "password": "testpass123"})
    logged_in.post("/ui/auth/mode", json={"mode": "forms"})


def test_season_no_hard_cap(mock_arr, logged_in, api_key, monkeypatch):
    """26-episode seasons must not stop after 5 second-chance searches."""
    from streamarr import config, indexers
    eps = [{"seasonNumber": 2026, "episodeNumber": n, "title": f"Episode Number {n:02d} Unique",
            "airDate": "2026-01-01", "episodeFileId": 0} for n in range(1, 27)]

    async def episodes(seriesId: int, x_api_key=None):
        return eps
    # patch the mock's episode route response via a plain function override
    from streamarr import arr as _arr
    monkeypatch.setattr(_arr, "season_episodes", lambda inst, q, season: [
        {"series": "Veritasium", "title": e["title"], "airDate": e["airDate"],
         "episode": e["episodeNumber"]} for e in eps])

    def fake_search(idx, text, limit=100, offset=0):
        # every per-episode search returns exactly its item
        for e in eps:
            if e["title"] in (text or ""):
                return [{"id": f"youtube:cap{e['episodeNumber']:09d}", "indexer_id": "yta",
                         "provider": "youtube", "series_title": "Veritasium",
                         "title": e["title"], "url": "https://example.invalid/e",
                         "published": 1752000000, "duration": 60, "ordinal": None,
                         "meta": {"exact_date": True}}]
        return []
    real = indexers.search
    monkeypatch.setattr(indexers, "search", fake_search)
    config.update("instances", [_inst()])
    try:
        r = logged_in.get(f"/newznab/yta/api?t=tvsearch&q=Veritasium&season=2026&apikey={api_key}")
        assert r.text.count("<item>") == 26
    finally:
        config.update("instances", [])
        monkeypatch.setattr(indexers, "search", real)


def test_fs_safe_names(logged_in, api_key):
    from streamarr import downloader
    safe = downloader._fs_safe("../../etc/passwd")
    assert ".." not in safe and "/" not in safe and "etc-passwd" in safe
    assert downloader._fs_safe(".hidden") == "hidden"
    assert downloader._fs_safe("a/b\\c") == "a-b-c"
    logged_in.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    r = logged_in.post("/ui/download", json={"url": "https://example.invalid/x",
                                             "name": "../../escape", "category": "../tv"})
    nzo = r.json()["nzo_id"]
    from streamarr import db
    j = db.job_get(nzo)
    assert ".." not in j["name"] and ".." not in j["category"]
    db.job_delete(nzo)


def test_security_headers(client):
    r = client.get("/ping")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_oversized_nzb_rejected(client, api_key):
    r = client.post(f"/sabnzbd/api?mode=addfile&apikey={api_key}",
                    files={"nzbfile": ("big.nzb", "x" * 1_100_000, "application/x-nzb")})
    assert r.json()["status"] is False and "large" in r.json()["error"]
