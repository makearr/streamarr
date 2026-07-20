def test_no_password_requirements(logged_in):
    # overwriting the account with a 1-char password is allowed
    r = logged_in.post("/ui/auth/setup", json={"username": "a", "password": "x"})
    assert r.status_code == 200
    r = logged_in.post("/ui/auth/setup", json={"username": "tester", "password": "testpass123"})
    assert r.status_code == 200


def test_empty_credentials_rejected(logged_in):
    assert logged_in.post("/ui/auth/setup", json={"username": "", "password": ""}).status_code == 400


def test_login_wrong_password(client):
    r = client.post("/ui/auth/login", json={"username": "tester", "password": "wrong"})
    assert r.status_code == 401


def test_mode_none_opens_access(logged_in, client):
    logged_in.post("/ui/auth/mode", json={"mode": "none"})
    r = client.get("/ui/queue", headers={"Cookie": ""})
    assert r.status_code == 200
    logged_in.post("/ui/auth/mode", json={"mode": "forms"})


def test_mode_validation(logged_in):
    assert logged_in.post("/ui/auth/mode", json={"mode": "bogus"}).status_code == 400


def test_api_key_header_accepted(client, api_key):
    r = client.get("/ui/status", headers={"X-Api-Key": api_key})
    assert r.status_code == 200


def test_newznab_rejects_missing_key(client):
    assert client.get("/newznab/yt/api?t=caps").status_code == 401


def test_apikey_rotate(logged_in):
    from streamarr import config
    old = config.get()["streamarr"]["api_key"]
    try:
        r = logged_in.post("/ui/apikey/rotate")
        assert r.status_code == 200
        assert "\u2026" in r.json()["api_key"].encode("unicode_escape").decode() or "…" in r.json()["api_key"]
        new = config.get()["streamarr"]["api_key"]
        assert new != old and len(new) == 32
        assert r.json()["api_key"] != new  # response only carries the masked form
        assert logged_in.get(f"/newznab/yt/api?t=caps&apikey={old}").status_code == 401
        assert logged_in.get(f"/newznab/yt/api?t=caps&apikey={new}").status_code == 200
    finally:
        config.update("streamarr", {"api_key": old})  # restore for the session-scoped fixture
