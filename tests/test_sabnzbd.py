import re


def _nzb(client, api_key):
    return client.get(f"/newznab/yt/download/youtube:abc4567890X.nzb?apikey={api_key}").text


def test_version_and_get_config(client, api_key):
    assert client.get(f"/sabnzbd/api?mode=version&apikey={api_key}").json()["version"].startswith("1.")
    cfg = client.get(f"/sabnzbd/api?mode=get_config&apikey={api_key}").json()["config"]
    assert any(c["name"] == "tv" for c in cfg["categories"])


def test_addfile_enqueues_and_queue_lists(client, api_key, seeded_item):
    client.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    r = client.post(f"/sabnzbd/api?mode=addfile&cat=tv&apikey={api_key}",
                    files={"nzbfile": ("x.nzb", _nzb(client, api_key), "application/x-nzb")})
    assert r.json()["status"] is True
    nzo = r.json()["nzo_ids"][0]
    q = client.get(f"/sabnzbd/api?mode=queue&apikey={api_key}").json()["queue"]
    slot = next(s for s in q["slots"] if s["nzo_id"] == nzo)
    assert slot["cat"] == "tv" and "S01E012" in slot["filename"]
    client.get(f"/sabnzbd/api?mode=queue&name=delete&value={nzo}&apikey={api_key}")


def test_addfile_rejects_foreign_nzb(client, api_key):
    r = client.post(f"/sabnzbd/api?mode=addfile&apikey={api_key}",
                    files={"nzbfile": ("x.nzb", "<nzb>real usenet nzb</nzb>", "application/x-nzb")})
    assert r.json()["status"] is False


def test_reorder_and_pause_item(client, api_key, seeded_item):
    client.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    ids = []
    for _ in range(2):
        r = client.post(f"/sabnzbd/api?mode=addfile&cat=tv&apikey={api_key}",
                        files={"nzbfile": ("x.nzb", _nzb(client, api_key), "application/x-nzb")})
        ids.append(r.json()["nzo_ids"][0])
    client.get(f"/sabnzbd/api?mode=switch&value={ids[1]}&value2=0&apikey={api_key}")
    q = client.get(f"/sabnzbd/api?mode=queue&apikey={api_key}").json()["queue"]
    order = [s["nzo_id"] for s in q["slots"] if s["nzo_id"] in ids]
    assert order[0] == ids[1]
    for nzo in ids:
        client.get(f"/sabnzbd/api?mode=queue&name=delete&value={nzo}&apikey={api_key}")


def test_speedlimit(client, api_key):
    client.get(f"/sabnzbd/api?mode=config&name=speedlimit&value=2500&apikey={api_key}")
    from streamarr import config
    assert config.get()["downloads"]["speed_limit_kbps"] == 2500
    client.get(f"/sabnzbd/api?mode=config&name=speedlimit&value=0&apikey={api_key}")


def test_history_shape(client, api_key):
    h = client.get(f"/sabnzbd/api?mode=history&apikey={api_key}").json()["history"]
    assert "slots" in h
