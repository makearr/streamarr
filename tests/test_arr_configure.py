import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request

MOCK_PORT = 8877
MOCK_KEY = "mockarrkey000000000000000000000000"


def _mock_app(accept_host):
    app = FastAPI()
    store = {"indexer": [], "downloadclient": []}
    state = {"accept_host": accept_host}
    app.state.store = store
    app.state.cfg = state

    def _auth(x_api_key):
        if x_api_key != MOCK_KEY:
            raise HTTPException(401)

    def _validate(payload):
        fields = {f["name"]: f["value"] for f in payload.get("fields", [])}
        target = fields.get("baseUrl") or f"http://{fields.get('host')}:{fields.get('port')}"
        host = target.split("//")[-1].split(":")[0].split("/")[0]
        if host != state["accept_host"]:
            raise HTTPException(400, detail=[{"errorMessage": f"Unable to connect to {target}"}])
        if "host" in fields and fields.get("urlBase", "") != "sabnzbd":
            # real arrs call {host}:{port}/{urlBase}/api — an empty urlBase used to hit the
            # SPA catch-all and return HTML ("Unknown Version"), which is the bug this guards
            raise HTTPException(400, detail=[{"propertyName": "Version",
                                              "errorMessage": "Unknown Version: "}])

    for ver in ("v1", "v3"):
        for kind in ("indexer", "downloadclient"):
            def make(kind=kind):
                async def list_items(x_api_key: str = Header(None)):
                    _auth(x_api_key)
                    return store[kind]

                async def create(request: Request, x_api_key: str = Header(None)):
                    _auth(x_api_key)
                    payload = await request.json()
                    _validate(payload)
                    payload["id"] = len(store[kind]) + 1
                    store[kind].append(payload)
                    return payload

                async def update(item_id: int, request: Request, x_api_key: str = Header(None)):
                    _auth(x_api_key)
                    payload = await request.json()
                    _validate(payload)
                    for i, e in enumerate(store[kind]):
                        if e["id"] == item_id:
                            payload["id"] = item_id
                            store[kind][i] = payload
                            return payload
                    raise HTTPException(404)
                return list_items, create, update
            li, cr, up = make()
            app.get(f"/api/{ver}/{kind}")(li)
            app.post(f"/api/{ver}/{kind}")(cr)
            app.put(f"/api/{ver}/{kind}/{{item_id}}")(up)

        @app.get(f"/api/{ver}/system/status")
        async def status(x_api_key: str = Header(None)):
            _auth(x_api_key)
            return {"appName": "MockArr", "version": "4.0.0.0"}

        @app.get(f"/api/{ver}/series")
        async def series(x_api_key: str = Header(None)):
            _auth(x_api_key)
            return [{"id": 1, "title": "Veritasium"}]

        @app.get(f"/api/{ver}/episode")
        async def episodes(seriesId: int, x_api_key: str = Header(None)):
            _auth(x_api_key)
            return [{"seasonNumber": 2026, "episodeNumber": 17,
                     "title": "The Scariest Chart in Electrical Engineering",
                     "airDate": "2026-07-08", "episodeFileId": 0},
                    {"seasonNumber": 2026, "episodeNumber": 18,
                     "title": "Why Bridges Don't Fall Down",
                     "airDate": "2026-07-15", "episodeFileId": 0}]

    return app


@pytest.fixture(scope="module")
def mock_arr():
    app = _mock_app(accept_host="streamarr")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=MOCK_PORT, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", MOCK_PORT), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield app
    server.should_exit = True


def _inst(arr_type="sonarr"):
    return {"name": "Mock", "type": arr_type, "url": f"127.0.0.1:{MOCK_PORT}",
            "api_key": MOCK_KEY, "verify_ssl": False, "enabled": True,
            "auto_configure": False, "own_url": "", "indexer_ids": []}


def test_arr_connection_test(mock_arr, logged_in):
    from streamarr import arr
    ok, detail = arr.test(_inst())
    assert ok and "MockArr" in detail


def test_configure_creates_indexer_and_client(mock_arr, logged_in):
    from streamarr import arr, indexers
    mock_arr.state.store["indexer"].clear()
    mock_arr.state.store["downloadclient"].clear()
    res = arr.configure(_inst(), indexers.get_indexer("yt"))
    assert res and all(r["ok"] for r in res)
    assert res[0]["used_url"].startswith("http://streamarr:")
    idx = mock_arr.state.store["indexer"][0]
    fields = {f["name"]: f["value"] for f in idx["fields"]}
    assert fields["baseUrl"].endswith("/newznab/yt")
    assert fields["apiPath"] == "/api"
    dc = mock_arr.state.store["downloadclient"][0]
    dfields = {f["name"]: f["value"] for f in dc["fields"]}
    assert dfields["host"] == "streamarr" and dfields["port"] == 8585
    assert dfields["urlBase"] == "sabnzbd"
    assert dfields["tvCategory"] == "tv"


def test_configure_upserts_on_second_run(mock_arr, logged_in):
    from streamarr import arr, indexers
    n_before = len(mock_arr.state.store["indexer"])
    res = arr.configure(_inst(), indexers.get_indexer("yt"))
    assert all(r["ok"] for r in res)
    assert len(mock_arr.state.store["indexer"]) == n_before  # updated, not duplicated
    assert any(r["detail"] == "updated" for r in res)


def test_configure_candidate_fallback(mock_arr, logged_in):
    import socket as _s
    from streamarr import arr, indexers
    mock_arr.state.cfg["accept_host"] = _s.gethostname()  # docker-DNS candidate now "unreachable"
    try:
        res = arr.configure(_inst(), indexers.get_indexer("yt"))
        assert res and all(r["ok"] for r in res)
        assert _s.gethostname() in res[0]["used_url"]
    finally:
        mock_arr.state.cfg["accept_host"] = "streamarr"


def test_configure_readarr_book_fields(mock_arr, logged_in):
    from streamarr import arr, indexers
    mock_arr.state.store["downloadclient"].clear()
    res = arr.configure(_inst("readarr"), indexers.get_indexer("yt"))
    assert all(r["ok"] for r in res)
    dfields = {f["name"]: f["value"] for f in mock_arr.state.store["downloadclient"][0]["fields"]}
    assert dfields["bookCategory"] == "books"
    assert "recentBookPriority" in dfields


def test_configure_bad_key_no_candidate_loop(mock_arr, logged_in):
    from streamarr import arr, indexers
    inst = _inst()
    inst["api_key"] = "wrong"
    res = arr.configure(inst, indexers.get_indexer("yt"))
    assert res and not any(r.get("ok") for r in res)


def test_arr_title_based_episode_naming(mock_arr, logged_in, api_key):
    """The Veritasium case: Sonarr asks for S2026E17 (TVDB year-season); only the episode
    TITLE can identify the video. Streamarr must resolve it via the arr and rename."""
    from streamarr import config, db
    db.cache_upsert([{
        "id": "youtube:verita000001", "indexer_id": "yta", "provider": "youtube",
        "series_title": "Veritasium",
        "title": "The Scariest Chart in Electrical Engineering",
        "url": "https://example.invalid/verita", "published": 1752537600,
        "duration": 1200, "ordinal": 17, "meta": {"exact_date": True}}])
    config.update("instances", [_inst()])
    try:
        r = logged_in.get(f"/newznab/yta/api?t=tvsearch&q=Veritasium&season=2026&ep=17&apikey={api_key}")
        assert r.status_code == 200
        assert "S2026E017" in r.text
        assert "Veritasium - S2026E017 - The Scariest Chart in Electrical Engineering" in r.text
    finally:
        config.update("instances", [])


def test_second_chance_search_by_episode_title(mock_arr, logged_in, api_key, monkeypatch):
    """Older videos fall off search relevance — the episode title itself must be searched."""
    from streamarr import config, db, indexers
    # nothing about this episode in the series-search cache path…
    hidden = {"id": "youtube:hidden000001", "indexer_id": "yta", "provider": "youtube",
              "series_title": "Veritasium",
              "title": "The Scariest Chart in Electrical Engineering",
              "url": "https://example.invalid/hid", "published": 1752537600,
              "duration": 1200, "ordinal": None, "meta": {"exact_date": True}}
    real_search = indexers.search

    def fake_search(idx, text, limit=100, offset=0):
        if "Scariest" in (text or ""):     # second-chance query = episode title
            return [hidden]
        return []                          # series search finds nothing
    monkeypatch.setattr(indexers, "search", fake_search)
    db.cache_upsert([hidden])
    config.update("instances", [_inst()])
    try:
        r = logged_in.get(f"/newznab/yta/api?t=tvsearch&q=Veritasium&season=2026&ep=17&apikey={api_key}")
        assert "S2026E017" in r.text and "Scariest Chart" in r.text
    finally:
        config.update("instances", [])
        monkeypatch.setattr(indexers, "search", real_search)


def test_empty_result_when_episode_not_found(mock_arr, logged_in, api_key):
    """Unmatched season/ep must return an EMPTY feed, not 25 garbage releases."""
    from streamarr import config
    config.update("instances", [_inst()])
    try:
        r = logged_in.get(f"/newznab/yta/api?t=tvsearch&q=Veritasium&season=2026&ep=99&apikey={api_key}")
        assert r.status_code == 200
        assert "<item>" not in r.text
    finally:
        config.update("instances", [])


def test_nzb_name_keeps_arr_episode_identity(mock_arr, logged_in, api_key):
    """The grabbed NZB (= SAB job & folder name) must carry the same S<year>E<n> name as the
    search result, or the import cannot map the file."""
    from streamarr import config
    config.update("instances", [_inst()])
    try:
        logged_in.get(f"/newznab/yta/api?t=tvsearch&q=Veritasium&season=2026&ep=17&apikey={api_key}")
        r = logged_in.get(f"/newznab/yta/download/youtube:verita000001.nzb?apikey={api_key}")
        assert r.status_code == 200
        assert "S2026E017" in r.text
        assert "The Scariest Chart" in r.text
    finally:
        config.update("instances", [])
