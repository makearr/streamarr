"""End-to-end guard for the automatic download chain, replicating Sonarr's own steps:
search -> validate the downloaded NZB the way Sonarr does -> push to the SAB client ->
job appears in the queue with the correct name."""
import xml.etree.ElementTree as ET

from streamarr import config, db

NS = "{http://www.newzbin.com/DTD/2003/nzb}"


def sonarr_validate_nzb(content):
    """Mirror of Sonarr's NzbValidationService: root must be nzb, at least one file
    with at least one segment, positive segment bytes."""
    root = ET.fromstring(content)
    assert root.tag == f"{NS}nzb", "root element must be nzb"
    files = root.findall(f"{NS}file")
    assert files, "Invalid NZB: No files"
    for f in files:
        segs = f.findall(f"{NS}segments/{NS}segment")
        assert segs, "Invalid NZB: file without segments"
        for seg in segs:
            assert int(seg.get("bytes")) > 0
            assert seg.text and "@" in seg.text
        assert f.get("subject") and f.get("date") and f.get("poster")
        assert f.findall(f"{NS}groups/{NS}group")


def _seed():
    db.cache_upsert([{
        "id": "youtube:chainvid0001", "indexer_id": "yt", "provider": "youtube",
        "series_title": "Chain Channel", "title": "Chain Episode",
        "url": "https://example.invalid/chain", "published": 1752000000,
        "duration": 700, "ordinal": 3, "meta": {"exact_date": True}}])


def test_nzb_passes_sonarr_validation(client, api_key):
    _seed()
    r = client.get(f"/newznab/yt/download/youtube:chainvid0001.nzb?apikey={api_key}")
    assert r.status_code == 200
    sonarr_validate_nzb(r.text)
    assert "STREAMARR:" in r.text


def test_full_grab_chain(client, api_key):
    _seed()
    client.get(f"/sabnzbd/api?mode=pause&apikey={api_key}")
    # 1. Sonarr searches
    r = client.get(f"/newznab/yt/api?t=tvsearch&q=Chain&season=1&ep=3&apikey={api_key}")
    assert "S01E003" in r.text and "Chain Episode" in r.text
    # 2. Sonarr downloads + validates the NZB
    nzb = client.get(f"/newznab/yt/download/youtube:chainvid0001.nzb?apikey={api_key}").text
    sonarr_validate_nzb(nzb)
    # 3. Sonarr pushes it to the SAB client
    r = client.post(f"/sabnzbd/api?mode=addfile&cat=tv&apikey={api_key}",
                    files={"nzbfile": ("chain.nzb", nzb, "application/x-nzb")})
    assert r.json()["status"] is True
    nzo = r.json()["nzo_ids"][0]
    # 4. the job is queued under the exact release name Sonarr expects to import
    q = client.get(f"/sabnzbd/api?mode=queue&apikey={api_key}").json()["queue"]
    slot = next(s for s in q["slots"] if s["nzo_id"] == nzo)
    assert "Chain Channel - S01E003 - Chain Episode" in slot["filename"]
    client.get(f"/sabnzbd/api?mode=queue&name=delete&value={nzo}&apikey={api_key}")


def test_series_aware_pick_rejects_topic_channel(client, api_key):
    """The Epic-Mountain case: identical episode titles from a music Topic channel must not
    win over the real series upload."""
    db.cache_upsert([
        {"id": "youtube:epicmtn00001", "indexer_id": "yta", "provider": "youtube",
         "series_title": "Epic Mountain - Topic", "title": "Germany Is Over",
         "url": "https://example.invalid/music", "published": 1752200000,
         "duration": 180, "ordinal": None, "meta": {"exact_date": True}},
        {"id": "youtube:kurzreal0001", "indexer_id": "yta", "provider": "youtube",
         "series_title": "Veritasium", "title": "The Scariest Chart in Electrical Engineering",
         "url": "https://example.invalid/real2", "published": 1752100000,
         "duration": 1200, "ordinal": None, "meta": {"exact_date": True}}])
    from streamarr.api.newznab import _pick
    info = {"series": "Veritasium", "title": "The Scariest Chart in Electrical Engineering"}
    items = db.cache_search("yta")
    hit = _pick(items, info)
    assert hit and hit["series_title"] == "Veritasium"
    # ambiguity across two wrong channels -> reject
    info2 = {"series": "Kurzgesagt - In a Nutshell", "title": "Germany Is Over"}
    two_wrong = [
        {"series_title": "Epic Mountain - Topic", "title": "Germany Is Over"},
        {"series_title": "Random Reuploads", "title": "GERMANY IS OVER"},
    ]
    assert _pick(two_wrong, info2) is None
