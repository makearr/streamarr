from streamarr import naming


def test_caps(client, api_key):
    r = client.get(f"/newznab/yt/api?t=caps&apikey={api_key}")
    assert r.status_code == 200
    assert "<caps>" in r.text and 'id="5000"' in r.text


def test_unknown_indexer_404(client, api_key):
    assert client.get(f"/newznab/nope/api?t=caps&apikey={api_key}").status_code == 404


def test_search_serves_cached_items(client, api_key, seeded_item):
    r = client.get(f"/newznab/yt/api?t=tvsearch&q=Optik&apikey={api_key}")
    assert r.status_code == 200
    assert "S01E012" in r.text and "Physik Kanal" in r.text
    assert "/newznab/yt/download/youtube:abc4567890X.nzb" in r.text


def test_episode_filter_absolute(client, api_key, seeded_item):
    r = client.get(f"/newznab/yt/api?t=tvsearch&q=Optik&season=1&ep=12&apikey={api_key}")
    assert "S01E012" in r.text


def test_nzb_payload(client, api_key, seeded_item):
    r = client.get(f"/newznab/yt/download/youtube:abc4567890X.nzb?apikey={api_key}")
    assert r.status_code == 200
    assert "STREAMARR:" in r.text and '"provider": "youtube"' in r.text
