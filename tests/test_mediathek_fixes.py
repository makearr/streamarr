"""Regression tests for the 'Query successful, but no results' arr indexer-test failure."""
import json

from streamarr.providers import ardzdf, mediathek


def test_mvw_body_omits_empty_query():
    body = mediathek.build_body("", 50, 0)
    assert "queries" not in body          # empty-string query matches NOTHING on MVW
    body = mediathek.build_body("tagesschau", 50, 0)
    assert body["queries"][0]["query"] == "tagesschau"


def test_mvw_empty_query_returns_latest(monkeypatch, logged_in):
    """Simulate the arr test (t=tvsearch, no q): the MVW request must carry no queries
    clause and the results must flow through."""
    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"result": {"results": [{
                "channel": "ZDF", "topic": "heute journal", "title": "heute journal vom 17.07.",
                "timestamp": 1752700000, "duration": 1800,
                "url_video_hd": "https://example.invalid/hj.mp4"}]}}

    def fake_post(url, content=None, **kw):
        captured["body"] = json.loads(content)
        return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    items = mediathek.search("", "mt", limit=50)
    assert "queries" not in captured["body"]
    assert items and items[0]["series_title"] == "heute journal"


def test_ardzdf_empty_query_skips_direct_api(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("direct API must not be called for empty queries")
    monkeypatch.setattr(ardzdf, "_zdf_direct", boom)
    monkeypatch.setattr(ardzdf, "mediathek",
        type("M", (), {"search": staticmethod(lambda text, iid, limit=0: [
            {"id": "mediathek:1", "indexer_id": "zdf1", "provider": "mediathek",
             "series_title": "heute", "title": "heute 19 Uhr", "url": "https://example.invalid/h",
             "published": 1752700000, "duration": 900, "ordinal": None,
             "meta": {"channel": "ZDF"}},
            {"id": "mediathek:2", "indexer_id": "zdf1", "provider": "mediathek",
             "series_title": "Tagesschau", "title": "tagesschau", "url": "https://example.invalid/t",
             "published": 1752700000, "duration": 900, "ordinal": None,
             "meta": {"channel": "ARD"}},
        ])})())
    items = ardzdf.search("zdf", "", {"id": "zdf1"}, limit=10)
    assert len(items) == 1 and items[0]["meta"]["channel"] == "ZDF"
    assert items[0]["provider"] == "zdf"


def test_rss_carries_subcategories(client, api_key, seeded_item):
    r = client.get(f"/newznab/yt/api?t=tvsearch&q=Optik&apikey={api_key}")
    assert '<newznab:attr name="category" value="5000"/>' in r.text
    assert '<newznab:attr name="category" value="5040"/>' in r.text
    assert "<category>5040</category>" in r.text


def test_expand_cats():
    from streamarr.indexers import _expand_cats
    assert _expand_cats([5000, 2000]) == [5000, 5040, 2000, 2040]
    assert _expand_cats([3000]) == [3000, 3010]
    assert _expand_cats([6000]) == [6000]
