import time

from streamarr import config, db, subscriptions


def _mksub(**kw):
    sub = dict(config.SUB_DEFAULTS)
    sub.update({"id": "chan1", "title": "PathChan", "url": "https://example.invalid/c",
                "provider": "youtube", "path": "/downloads/custom/PathChan",
                "interval_minutes": 30, "category": "tv"})
    sub.update(kw)
    return sub


ITEMS = [{"id": "youtube:pc0000000001", "indexer_id": "sub_chan1", "provider": "youtube",
          "series_title": "PathChan", "title": "First", "url": "https://example.invalid/1",
          "published": 1752000000, "duration": 10, "ordinal": 1, "meta": {"exact_date": True}}]


def test_subs_crud_validation(logged_in):
    assert logged_in.post("/ui/subs", json=[{"id": "BAD!", "url": "x", "provider": "youtube"}]).status_code == 400
    assert logged_in.post("/ui/subs", json=[{"id": "ok1", "url": "", "provider": "youtube"}]).status_code == 400
    r = logged_in.post("/ui/subs", json=[_mksub()])
    assert r.status_code == 200 and r.json()[0]["path"] == "/downloads/custom/PathChan"
    logged_in.post("/ui/subs", json=[])


def test_sub_custom_path_reaches_job(monkeypatch, logged_in, api_key):
    logged_in.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    listing = {"items": list(ITEMS)}
    monkeypatch.setattr(subscriptions, "_list", lambda idx, sub: listing["items"])
    monkeypatch.setitem(config.get(), "subs", [_mksub()])
    monkeypatch.setitem(config.get(), "indexers", [])
    assert subscriptions.process(force=True) == 0   # baseline
    listing["items"] = listing["items"] + [dict(ITEMS[0], id="youtube:pc0000000002", title="Second")]
    assert subscriptions.process(force=True) == 1
    job = next(j for j in db.jobs_active() if "Second" in j["name"])
    assert job["outdir"] == "/downloads/custom/PathChan"
    assert job["category"] == "tv"
    db.job_delete(job["nzo_id"])


def test_per_sub_interval_gating(monkeypatch):
    monkeypatch.setitem(config.get(), "subs", [_mksub(id="chan2", interval_minutes=999)])
    monkeypatch.setitem(config.get(), "indexers", [])
    db.subs_set_run("sub_chan2")
    assert not subscriptions.due_subs(force=False)   # just ran, not due
    db.execute("UPDATE subs_runs SET last_run=? WHERE sub_id=?", (int(time.time()) - 999*60 - 1, "sub_chan2"))
    assert len(subscriptions.due_subs(force=False)) == 1


def test_per_sub_check_arr_override(monkeypatch, logged_in, api_key):
    logged_in.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    items = [dict(ITEMS[0], id="youtube:ovr000000001", title="Override item",
                  indexer_id="sub_chan3", series_title="OvrChan")]
    db.subs_mark("youtube:ovrbaseline", "sub_chan3", "OvrChan", downloaded=False)
    sub = _mksub(id="chan3", title="OvrChan", check_arr="off")
    monkeypatch.setitem(config.get(), "subs", [sub])
    monkeypatch.setitem(config.get(), "indexers", [])
    monkeypatch.setattr(subscriptions, "_list", lambda i, s: items)
    monkeypatch.setattr(subscriptions, "_arr_already_has", lambda t: "Sonarr")  # would skip…
    config.update("subscriptions", {"check_arr": True})
    assert subscriptions.process(force=True) == 1  # …but per-sub override forces download
    for j in db.jobs_active():
        if "Override item" in j["name"]:
            db.job_delete(j["nzo_id"])
