import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_tmp = tempfile.mkdtemp(prefix="streamarr-test-")
os.environ["STREAMARR_CONFIG_DIR"] = _tmp
os.environ["STREAMARR_DOWNLOADS_DIR"] = os.path.join(_tmp, "downloads")

from streamarr import config, db  # noqa: E402
from streamarr.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def api_key(client):
    return config.get()["streamarr"]["api_key"]


@pytest.fixture(scope="session")
def logged_in(client):
    r = client.post("/ui/auth/setup", json={"username": "tester", "password": "testpass123"})
    assert r.status_code == 200
    return client


@pytest.fixture()
def seeded_item():
    item = {
        "id": "youtube:abc4567890X", "indexer_id": "yt", "provider": "youtube",
        "series_title": "Physik Kanal", "title": "Folge über Optik", "url": "https://example.invalid/v",
        "published": 1752000000, "duration": 900, "ordinal": 12, "meta": {"channel": "Physik Kanal"},
    }
    db.cache_upsert([item])
    return item


@pytest.fixture(scope="session", autouse=True)
def default_indexers(logged_in):
    r = logged_in.post("/ui/indexers", json=[
        {"id": "yt", "name": "YT", "provider": "youtube", "media": "video",
         "naming": "absolute", "categories": [5000], "enabled": True},
        {"id": "mt", "name": "MT", "provider": "mediathek", "media": "video",
         "naming": "auto", "categories": [5000], "enabled": True},
        {"id": "yta", "name": "YT-Arr", "provider": "youtube", "media": "video",
         "naming": "arr", "categories": [5000], "enabled": True},
        {"id": "yts", "name": "YT-Strict", "provider": "youtube", "media": "video",
         "naming": "sxxeyy", "categories": [5000], "enabled": True},
        {"id": "ph", "name": "PH", "provider": "site", "site_preset": "pornhub",
         "media": "video", "naming": "date", "categories": [6000], "enabled": True},
        {"id": "ytm", "name": "YT Music", "provider": "site", "site_preset": "ytmusic",
         "media": "audio", "naming": "date", "categories": [3000], "enabled": True},
    ])
    assert r.status_code == 200
