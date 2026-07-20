from streamarr import config, db, naming


def test_proxy_bypass_local(logged_in):
    config.update("proxy", {"enabled": True, "host": "proxy.example.com", "port": 3128,
                            "bypass_local": True, "ignored_addresses": "*.lan, media?.example.org"})
    assert config.proxy_for("http://192.168.1.10:8989") is None
    assert config.proxy_for("http://sonarr:8989") is None          # bare docker hostname
    assert config.proxy_for("http://nas.lan:5000") is None         # ignored pattern
    assert config.proxy_for("http://media1.example.org") is None   # wildcard pattern
    assert config.proxy_for("https://www.youtube.com/watch") is not None
    config.update("proxy", {"enabled": False})


def test_quality_per_indexer_overrides_global(logged_in):
    idx = {"quality": {"max_resolution": 720, "audio_format": ""}}
    q = config.quality_for(idx)
    assert q["max_resolution"] == 720
    assert q["audio_format"] == config.get()["quality"]["audio_format"]  # empty = inherit


def test_public_url_candidates_order(logged_in):
    config.update("streamarr", {"public_url": "http://10.0.0.5:8585"})
    c = config.public_url_candidates("http://override:8585")
    assert c[0] == "http://override:8585" and c[1] == "http://10.0.0.5:8585"
    assert "http://streamarr:8585" in c
    config.update("streamarr", {"public_url": ""})
    assert config.public_url_candidates()[0] == "http://streamarr:8585"


def test_release_group_suffix():
    item = {"series_title": "Kanal", "title": "Video", "ordinal": 4, "published": None, "meta": {}}
    assert naming.release_title(item, "absolute").endswith("-Streamarr")


def test_queue_priority_action(logged_in, seeded_item, api_key):
    logged_in.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    nzo = logged_in.post("/ui/grab", json={"indexer_id": "yt", "item_id": seeded_item["id"]}).json()["nzo_id"]
    r = logged_in.post("/ui/queue/priority", json={"nzo_id": nzo, "priority": 2})
    assert r.status_code == 200
    assert db.job_get(nzo)["priority"] == 2
    logged_in.post("/ui/queue/delete", json={"nzo_id": nzo})


def test_new_defaults(logged_in):
    assert config.DEFAULTS["ratelimit"]["rate_limit_sleep"] == 300
    assert config.DEFAULTS["ytdlp"]["auto_update"] is True
    assert config.INSTANCE_DEFAULTS["verify_ssl"] is False


def test_sab_categories_extended(client, api_key):
    cfg = client.get(f"/sabnzbd/api?mode=get_config&apikey={api_key}").json()["config"]
    names = [c["name"] for c in cfg["categories"]]
    assert {"books", "audiobooks", "podcasts"} <= set(names)


def test_presets_include_audio_types(logged_in):
    d = logged_in.get("/ui/presets").json()
    assert d["sites"]["podcast"]["media"] == "audio"
    assert d["sites"]["audiobook"]["audio_format"] == "m4b"
    assert any(c[0] == 3030 for c in d["categories"])


def test_streamarr_settings_section(logged_in):
    r = logged_in.post("/ui/settings/streamarr", json={"public_url": "10.0.0.9:8585", "api_key": "EVIL"})
    assert r.status_code == 200
    cfg = config.get()["streamarr"]
    assert cfg["public_url"] == "http://10.0.0.9:8585"
    assert cfg["api_key"] != "EVIL"  # only whitelisted keys accepted
    logged_in.post("/ui/settings/streamarr", json={"public_url": ""})


def test_update_ytdlp_change_detection(monkeypatch):
    import subprocess
    from streamarr import maintenance

    class R:
        def __init__(self, out):
            self.returncode, self.stdout, self.stderr = 0, out, ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R("Successfully installed yt-dlp-2026.8.1"))
    ok, changed, detail = maintenance.update_ytdlp()
    assert ok and changed and "2026.8.1" in detail
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R("Requirement already satisfied: yt-dlp"))
    ok, changed, detail = maintenance.update_ytdlp()
    assert ok and not changed


def test_format_string_has_unconditional_fallback():
    from streamarr.providers.youtube import format_string
    q = {"max_resolution": 1080, "max_fps": 60, "video_format": "mp4",
         "audio_codec": "aac", "audio_format": "m4a"}
    assert format_string(q, "video").endswith("/best")
    assert format_string(q, "audio").endswith("/best")


def test_history_pagination(client, api_key, logged_in):
    from streamarr import db
    import time as _t
    for n in range(25):
        db.job_save({"nzo_id": f"SAR_hist{n:08d}", "name": f"Hist {n}", "category": "tv",
                     "indexer_id": "yt", "provider": "youtube", "url": "https://example.invalid",
                     "media": "video", "status": "Completed", "priority": 0, "position": 0,
                     "bytes_total": 100, "bytes_done": 100, "storage": "/x",
                     "fail_message": None, "completed": int(_t.time()) + n})
    try:
        p1 = logged_in.get("/ui/history?limit=20&offset=0").json()
        assert p1["total"] >= 25 and len(p1["items"]) == 20
        p2 = logged_in.get("/ui/history?limit=20&offset=20").json()
        assert len(p2["items"]) >= 5
        assert p1["items"][0]["nzo_id"] != p2["items"][0]["nzo_id"]
        assert logged_in.get("/ui/history?limit=999").json()["limit"] == 999  # served capped at 100
        assert len(logged_in.get("/ui/history?limit=999").json()["items"]) <= 100
    finally:
        for n in range(25):
            db.job_delete(f"SAR_hist{n:08d}")


def test_eta_from_rolling_average(monkeypatch):
    import time as _t
    from streamarr import downloader
    nzo = "SAR_etatest0001"
    now = _t.time()
    downloader._speed_hist[nzo] = [(now - 60, 0), (now - 30, 30_000_000), (now, 60_000_000)]
    assert abs(downloader._avg_speed(nzo) - 1_000_000) < 1e-6  # 60 MB over 60 s
    downloader._speed_hist[nzo] = [(now - 200, 0), (now, 1)]   # stale samples pruned
    assert downloader._avg_speed(nzo) == 0
    downloader._speed_hist.pop(nzo, None)


def test_sab_timeleft_format():
    from streamarr.api.sabnzbd import _timeleft
    assert _timeleft(None) == "0:00:00"
    assert _timeleft(59) == "0:00:59"
    assert _timeleft(3725) == "1:02:05"
