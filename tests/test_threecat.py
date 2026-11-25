"""3cat provider: catalog matching, episode titles, API params and the newznab path. No network."""
from datetime import datetime
from zoneinfo import ZoneInfo

import time

import httpx
import pytest

from streamarr import arr, config, naming
from streamarr.providers import threecat

IDX = {"id": "tc", "provider": "3cat", "naming": "auto", "media": "video", "categories": [5000]}

PROGRAMS = {"resposta": {"items": {"lletra": [
    {"item": [
        {"id": "126706100", "titol": "Plats bruts", "nombonic": "plats-bruts", "tipologia": "Programa"},
        {"id": "56237", "titol": "Com si fos ahir", "nombonic": "com-si-fos-ahir", "tipologia": "Serie"},
    ]},
    {"item": [
        {"id": "9001", "titol": "El foraster de la nit", "nombonic": "foraster-nit", "tipologia": "Serie"},
        {"id": "9002", "titol": "El foraster", "nombonic": "el-foraster", "tipologia": "Serie"},
        {"id": "9003", "titol": "Més enllà", "nombonic": "mes-enlla", "tipologia": "Programa"},
        {"id": 74616, "titol": "La casa nostra", "nombonic": "la-casa-nostra", "tipologia": "Serie"},
    ]},
]}}}

V_S10E54 = {"id": 6437547, "programa": "Com si fos ahir", "permatitle": "Capitol 54",
            "titol": "Com si fos ahir", "nom_friendly": "t10xc54", "capitol": 1856,
            "capitol_temporada": 54, "temporades": [{"id": "PUTEMP_10", "desc": "10a Temporada", "main": True}],
            "data_emissio": "25/11/2026 16:05:00", "durada": "00:28:45:00",
            "geolocalitzacions": [{"id": "TOTS", "desc": "No geolocalitzat"}]}
V_S09E01 = {"id": 5000001, "programa": "Com si fos ahir", "permatitle": "Capitol 1",
            "titol": "Com si fos ahir", "nom_friendly": "t9xc01", "capitol": 1500,
            "capitol_temporada": 1, "temporades": [{"id": "PUTEMP_9", "desc": "9a Temporada", "main": True}],
            "data_emissio": "01/01/2020 10:00:00", "durada": "00:30:00:00",
            "geolocalitzacions": [{"id": "ES", "desc": "Espanya"}]}
V_CASA = {"id": 6406141, "programa": "La casa nostra", "permatitle": "El nostre podcast",
          "titol": "La casa nostra", "nom_friendly": "t2xc2", "capitol": 16, "capitol_temporada": 2,
          "temporades": [{"id": "PUTEMP_2", "desc": "2a Temporada", "main": True}],
          "data_emissio": "08/06/2026 08:00:00", "durada": "00:24:00:00",
          "geolocalitzacions": [{"id": "TOTS", "desc": "No geolocalitzat"}]}
WD_ENTITY = {"entities": {"Q134096823": {
    "labels": {"ca": {"language": "ca", "value": "La casa nostra"}},
    "claims": {"P4835": [{"mainsnak": {"datavalue": {"value": "469193", "type": "string"}}}],
               "P1476": [{"mainsnak": {"datavalue": {"value": {"text": "La casa nostra", "language": "ca"}}}}]}}}}
V_LATEST = {"id": 7000001, "programa": "Telenotícies", "permatitle": "Edició vespre",
            "titol": "Telenotícies", "nom_friendly": "tvespre", "capitol": -1,
            "capitol_temporada": -1, "temporades": [],
            "data_emissio": "24/11/2026 21:00:00", "durada": "00:10:00:00",
            "geolocalitzacions": [{"id": "TOTS", "desc": "No geolocalitzat"}]}


PROGRAMS_FLAT = [p for lt in PROGRAMS["resposta"]["items"]["lletra"] for p in lt["item"]]


class FakeResp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _page(items, pages=1):
    return {"resposta": {"items": {"item": items},
                         "paginacio": {"total_items": len(items), "pagina_actual": 1,
                                       "total_pagines": pages}}}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(threecat, "_catalog_cache", {"at": 0.0, "programs": []})
    monkeypatch.setattr(threecat, "_resolve_cache", {})
    monkeypatch.setattr(threecat.limiter, "wait", lambda provider: None)


@pytest.fixture()
def api(monkeypatch):
    """Fake 3cat API: records every call and serves a small catalog and episode lists."""
    calls = []

    def fake_get(url, params=None, **kw):
        params = dict(params or {})
        calls.append((url, params))
        if url == threecat.PROGRAMS_URL:
            return FakeResp(PROGRAMS)
        assert url == threecat.VIDEOS_URL, url
        if "programatv_id" not in params:
            return FakeResp(_page([V_LATEST]))
        if params["programatv_id"] == 74616:
            return FakeResp(_page([V_CASA]))
        if params["programatv_id"] != "56237":
            return FakeResp(_page([]))
        videos = [V_S10E54, V_S09E01]
        if "temporada" in params:  # the real endpoint filters by season; mimic it
            videos = [v for v in videos
                      if any(t["id"] == params["temporada"] for t in v["temporades"])]
        return FakeResp(_page(videos))

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


# ---------- program matching ----------

def test_match_ignores_case_and_accents():
    assert threecat._match_programs("MÉS ENLLÀ", PROGRAMS_FLAT)[0]["id"] == "9003"
    assert threecat._match_programs("mes enlla", PROGRAMS_FLAT)[0]["id"] == "9003"
    assert threecat._match_programs("plats-bruts", PROGRAMS_FLAT)[0]["id"] == "126706100"


def test_exact_title_returns_only_exact_matches():
    matches = threecat._match_programs("El foraster", PROGRAMS_FLAT)
    assert [m["id"] for m in matches] == ["9002"]


def test_word_match_is_whole_word_only():
    programs = [{"id": "1", "titol": "Sputnik", "nombonic": "sputnik"},
                {"id": "2", "titol": "Merlinari", "nombonic": "merlinari"},
                {"id": "3", "titol": "TN Tarragona", "nombonic": "tn-tarragona"}]
    assert threecat._match_programs("TN", programs)[0]["id"] == "3"
    assert [p["id"] for p in threecat._match_programs("tn", programs)] == ["3"]
    assert threecat._match_programs("Merlí", programs) == []


def test_word_match_when_not_exact():
    assert threecat._match_programs("foraster nit", PROGRAMS_FLAT)[0]["id"] == "9001"
    assert threecat._match_programs("nope nothing", PROGRAMS_FLAT) == []
    assert threecat._match_programs("", PROGRAMS_FLAT) == []


# ---------- titles, fields, URLs ----------

def test_season_search_tags_title_and_builds_page_url(api):
    items = threecat.search("Com si fos ahir", IDX, season=10)
    assert [it["title"] for it in items] == ["S10E54 - Capitol 54"]
    it = items[0]
    assert it["id"] == "3cat:6437547"
    assert it["url"] == "https://www.3cat.cat/3cat/t10xc54/video/6437547/"
    assert it["ordinal"] is None  # capitol is series-wide; the SxxEyy tag carries the number
    assert it["series_title"] == "Com si fos ahir"
    assert it["provider"] == "3cat" and it["indexer_id"] == "tc"
    assert it["meta"]["channel"] == "3cat" and it["meta"]["program_id"] == "56237"
    assert it["meta"]["geo"] == ["TOTS"]
    assert naming.parse_sxxeyy(it["title"]) == (10, 54)


def test_season_request_sends_temporada_and_newest_first(api):
    threecat.search("Com si fos ahir", IDX, season=10)
    url, params = api[-1]
    assert url == threecat.VIDEOS_URL
    assert params["programatv_id"] == "56237"
    assert params["temporada"] == "PUTEMP_10"
    assert params["ordre"] == "-data_emissio"
    assert params["tipus_contingut"] == "PPD"


def test_no_season_request_has_no_temporada_filter(api):
    threecat.search("Com si fos ahir", IDX, limit=5)
    _, params = api[-1]
    assert "temporada" not in params and params["items_pagina"] == 5


def test_season_pages_are_walked(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kw):
        params = dict(params or {})
        if url == threecat.PROGRAMS_URL:
            return FakeResp(PROGRAMS)
        calls.append(params["pagina"])
        page_item = V_S10E54 if params["pagina"] == 1 else dict(V_S10E54, id=6437548,
                                                                  nom_friendly="t10xc55",
                                                                  capitol_temporada=55)
        return FakeResp(_page([page_item], pages=2))

    monkeypatch.setattr(httpx, "get", fake_get)
    items = threecat.search("Com si fos ahir", IDX, season=10)
    assert calls == [1, 2]
    assert [it["title"] for it in items] == ["S10E54 - Capitol 54", "S10E55 - Capitol 54"]


def test_season_request_returns_whole_season_past_limit(monkeypatch):
    def fake_get(url, params=None, **kw):
        params = dict(params or {})
        if url == threecat.PROGRAMS_URL:
            return FakeResp(PROGRAMS)
        base = {"id": 100 + params["pagina"] * 10, "nom_friendly": "t10xc1",
                "programa": "Com si fos ahir", "permatitle": "Capitol", "capitol": 1,
                "capitol_temporada": 1, "temporades": [{"id": "PUTEMP_10"}]}
        pages = [dict(base, id=base["id"] + i, capitol_temporada=base["id"] + i) for i in (1, 2)]
        return FakeResp(_page(pages, pages=2))

    monkeypatch.setattr(httpx, "get", fake_get)
    items = threecat.search("Com si fos ahir", IDX, limit=2, season=10)
    assert len(items) == 4  # two pages of two; limit must not cut a season short


def test_title_without_episode_number_stays_plain():
    v = dict(V_S10E54, capitol_temporada=-1, temporades=[])
    it = threecat._to_item(v, IDX)
    assert it["title"] == "Capitol 54"
    assert naming.parse_sxxeyy(it["title"]) is None
    assert it["ordinal"] == 1856


def test_season_from_request_when_video_lacks_temporades():
    v = dict(V_S10E54, temporades=[])
    assert threecat._to_item(v, IDX, requested_season=10)["title"] == "S10E54 - Capitol 54"
    assert threecat._to_item(v, IDX)["title"] == "Capitol 54"


def test_date_and_duration_parsing():
    it = threecat._to_item(V_S10E54, IDX)
    expected = datetime(2026, 11, 25, 16, 5, tzinfo=ZoneInfo("Europe/Madrid")).timestamp()
    assert it["published"] == int(expected)
    assert it["duration"] == 28 * 60 + 45
    assert threecat._duration("bogus") is None


def test_geo_restriction_is_kept_in_meta():
    it = threecat._to_item(V_S09E01, IDX)
    assert it["meta"]["geo"] == ["ES"]


# ---------- empty query ----------

def test_empty_query_hits_latest_endpoint(api):
    items = threecat.search("", IDX, limit=10)
    url, params = api[-1]
    assert url == threecat.VIDEOS_URL
    assert "programatv_id" not in params
    assert params["tipus_contingut"] == "PPD" and params["ordre"] == "-data_emissio"
    assert [it["series_title"] for it in items] == ["Telenotícies"]
    assert items[0]["title"] == "Edició vespre"


def test_stale_catalog_served_when_refresh_fails(monkeypatch, api):
    threecat.search("El foraster", IDX, limit=3)  # fills the catalog
    threecat._catalog_cache["at"] = 0.0  # TTL expired

    def down(url, params=None, **kw):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(httpx, "get", down)
    assert [p["id"] for p in threecat._programs()] == [p["id"] for p in PROGRAMS_FLAT]


def test_empty_catalog_parse_is_not_cached(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, params=None, **kw: FakeResp({}))
    assert threecat._programs() == []
    assert threecat._catalog_cache["at"] == 0.0 and threecat._catalog_cache["programs"] == []


def test_first_catalog_failure_raises(monkeypatch):
    def down(url, params=None, **kw):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(httpx, "get", down)
    with pytest.raises(httpx.ConnectError):
        threecat._programs()


def test_catalog_is_cached_between_searches(api):
    threecat.search("El foraster", IDX, limit=3)
    threecat.search("Plats bruts", IDX, limit=3)
    assert sum(1 for url, _ in api if url == threecat.PROGRAMS_URL) == 1


# ---------- newznab end-to-end ----------

def test_newznab_tvsearch_season_and_episode(client, logged_in, api_key, api):
    tc = config._merge(config.INDEXER_DEFAULTS,
                       {"id": "tc", "name": "3cat", "provider": "3cat", "media": "video",
                        "naming": "auto", "categories": [5000], "enabled": True})
    config.update("indexers", config.get()["indexers"] + [tc])
    try:
        r = client.get(f"/newznab/tc/api?t=tvsearch&q=Com+si+fos+ahir&season=10&ep=54&apikey={api_key}")
        assert r.status_code == 200
        assert "Com si fos ahir - S10E054 - Capitol 54 [WEBDL-1080p]-Streamarr" in r.text
        assert "S10E054 - S10E54" not in r.text
        assert r.text.count("<item>") == 1
        assert any(p.get("temporada") == "PUTEMP_10" for _, p in api)
    finally:
        config.update("indexers", [i for i in config.get()["indexers"] if i["id"] != "tc"])


# ---------- title resolution: manual alias, then Wikidata via the Sonarr TheTVDB id ----------

class FakeSonarr:
    def __init__(self, series):
        self.series = series

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path, params=None):
        assert path == "/series"
        return FakeResp(self.series)


@pytest.fixture()
def sonarr(monkeypatch):
    """One enabled Sonarr holding 'Our House' (TVDB 469193); returns a setter for the series list."""
    series = [{"id": 1, "title": "Our House", "tvdbId": 469193}]
    monkeypatch.setattr(arr, "_client", lambda inst: FakeSonarr(series))
    monkeypatch.setattr(arr, "_series_cache", {})
    config.update("instances", [config._merge(config.INSTANCE_DEFAULTS, {
        "name": "Sonarr", "type": "sonarr", "url": "127.0.0.1:8989", "api_key": "k", "enabled": True})])
    yield series
    config.update("instances", [])


def _wikidata(params):
    """Happy path: P4835=469193 is Q134096823, whose Catalan label is 'La casa nostra'."""
    if params["action"] == "query":
        hit = [{"title": "Q134096823"}] if "469193" in params["srsearch"] else []
        return FakeResp({"query": {"search": hit}})
    return FakeResp(WD_ENTITY)


def _route_wikidata(monkeypatch, handler):
    """Send Wikidata calls to `handler`, everything else to the current fake (the 3cat API)."""
    base = httpx.get
    calls = []

    def routed(url, params=None, **kw):
        if url == threecat.WIKIDATA_API:
            calls.append(dict(params or {}))
            return handler(dict(params or {}))
        return base(url, params=params, **kw)
    monkeypatch.setattr(httpx, "get", routed)
    return calls


def _ourhouse_idx(aliases=()):
    return {**IDX, "aliases": [{"sonarr": a, "threecat": b} for a, b in aliases]}


def test_alias_resolves_without_sonarr_or_wikidata(api, monkeypatch, caplog):
    def no_sonarr(inst):
        raise AssertionError("alias must not reach Sonarr")
    monkeypatch.setattr(arr, "_client", no_sonarr)
    calls = _route_wikidata(monkeypatch, lambda p: 1 / 0)
    with caplog.at_level("INFO", logger="streamarr.threecat"):
        items = threecat.search("Our House", _ourhouse_idx([("our  HOUSE", "La casa nostra")]), season=2)
    assert calls == []
    assert [it["series_title"] for it in items] == ["Our House"]  # the Sonarr title, not the 3cat one
    assert "'Our House' -> 'La casa nostra' via alias" in caplog.text


def test_wikidata_resolves_title_through_sonarr_tvdb_id(api, sonarr, monkeypatch, caplog):
    calls = _route_wikidata(monkeypatch, _wikidata)
    with caplog.at_level("INFO", logger="streamarr.threecat"):
        items = threecat.search("Our House", _ourhouse_idx(), season=2)
    assert [c["action"] for c in calls] == ["query", "wbgetentities"]
    assert calls[1]["languages"] == "ca" and calls[1]["ids"] == "Q134096823"
    assert [it["title"] for it in items] == ["S02E02 - El nostre podcast"]
    assert [it["series_title"] for it in items] == ["Our House"]
    assert "'Our House' -> 'La casa nostra' via wikidata (tvdb 469193)" in caplog.text


def test_wikidata_hit_and_miss_are_cached(api, monkeypatch):
    calls = _route_wikidata(monkeypatch, _wikidata)
    assert threecat._wikidata_title(469193) == "La casa nostra"
    assert threecat._wikidata_title(469193) == "La casa nostra"
    assert len(calls) == 2  # one search + one entity fetch, then served from cache
    calls.clear()
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp({"query": {"search": []}}))
    assert threecat._wikidata_title(111) is None
    assert threecat._wikidata_title(111) is None
    assert threecat._resolve_cache[111][1] is None


def test_wikidata_falls_back_to_catalan_title_claim(api, monkeypatch):
    entity = {"entities": {"Q134096823": {"labels": {},
              "claims": {"P4835": [{"mainsnak": {"datavalue": {"value": "469193"}}}],
                         "P1476": [{"mainsnak": {"datavalue": {"value": {"text": "La casa nostra", "language": "ca"}}}}]}}}}
    _route_wikidata(monkeypatch, lambda p: FakeResp({"query": {"search": [{"title": "Q134096823"}]}})
                    if p["action"] == "query" else FakeResp(entity))
    assert threecat._wikidata_fetch(469193) == "La casa nostra"


def test_wikidata_failure_returns_empty_and_is_cached_briefly(api, sonarr, monkeypatch):
    def down(params):
        raise httpx.ConnectError("boom")
    calls = _route_wikidata(monkeypatch, down)
    assert threecat.search("Our House", _ourhouse_idx(), season=2) == []
    assert threecat.search("Our House", _ourhouse_idx(), season=2) == []
    assert len(calls) == 1  # the failure is served from cache until FAILURE_TTL passes
    assert threecat._resolve_cache[469193][0] - time.time() <= threecat.FAILURE_TTL
    threecat._resolve_cache[469193] = (0.0, None)  # expired: the next search retries
    _route_wikidata(monkeypatch, _wikidata)
    assert [it["title"] for it in threecat.search("Our House", _ourhouse_idx(), season=2)] == [
        "S02E02 - El nostre podcast"]


def test_sonarr_failure_is_cached_briefly(api, monkeypatch):
    def down(inst):
        raise httpx.ConnectError("sonarr down")
    monkeypatch.setattr(arr, "_client", down)
    config.update("instances", [config._merge(config.INSTANCE_DEFAULTS, {
        "name": "Sonarr", "type": "sonarr", "url": "127.0.0.1:8989", "api_key": "k", "enabled": True})])
    try:
        calls = _route_wikidata(monkeypatch, _wikidata)
        assert threecat.search("Our House", _ourhouse_idx(), season=2) == []
        assert threecat.search("Our House", _ourhouse_idx(), season=2) == []
        assert calls == []
    finally:
        config.update("instances", [])


def test_alias_skips_malformed_entries(api, monkeypatch):
    idx = {**IDX, "aliases": ["Our House = La casa nostra", None,
                              {"sonarr": "Our House", "threecat": "La casa nostra"}]}
    items = threecat.search("Our House", idx, season=2)
    assert [it["series_title"] for it in items] == ["Our House"]


def test_newznab_tvsearch_resolves_alias_to_sonarr_title(client, logged_in, api_key, api):
    tc = config._merge(config.INDEXER_DEFAULTS, {
        "id": "tc", "name": "3cat", "provider": "3cat", "media": "video", "naming": "auto",
        "categories": [5000], "enabled": True, "aliases": [{"sonarr": "Our House", "threecat": "La casa nostra"}]})
    config.update("indexers", config.get()["indexers"] + [tc])
    try:
        r = client.get(f"/newznab/tc/api?t=tvsearch&q=Our%20House&season=2&ep=2&apikey={api_key}")
        assert r.status_code == 200
        assert "Our House - S02E002 - El nostre podcast" in r.text
        assert "La casa nostra - " not in r.text
        assert r.text.count("<item>") == 1
    finally:
        config.update("indexers", [i for i in config.get()["indexers"] if i["id"] != "tc"])


def test_exact_match_never_calls_sonarr_or_wikidata(api, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("exact match must not resolve titles")
    monkeypatch.setattr(arr, "tvdb_id", boom)
    calls = _route_wikidata(monkeypatch, boom)
    items = threecat.search("Com si fos ahir", _ourhouse_idx(), season=10)
    assert len(items) == 1 and calls == []


def test_tvdb_id_prefers_exact_title_over_fuzzy(sonarr):
    sonarr[:] = [{"id": 2, "title": "House", "tvdbId": 73255},
                 {"id": 1, "title": "Our House", "tvdbId": 469193}]
    assert arr.tvdb_id("Our House") == 469193


def test_tvdb_id_accepts_exact_alternate_title(sonarr):
    sonarr[:] = [{"id": 2, "title": "House", "tvdbId": 73255},
                 {"id": 1, "title": "Casa", "tvdbId": 469193,
                  "alternateTitles": [{"title": "Our House"}]}]
    assert arr.tvdb_id("our house") == 469193


def test_tvdb_id_ambiguous_fuzzy_match_is_none(sonarr):
    sonarr[:] = [{"id": 2, "title": "House", "tvdbId": 73255},
                 {"id": 3, "title": "Our House Reloaded", "tvdbId": 2}]
    assert arr.tvdb_id("Our House") is None


def test_tvdb_id_single_fuzzy_match_is_used(sonarr):
    sonarr[:] = [{"id": 3, "title": "Our House Reloaded", "tvdbId": 2}]
    assert arr.tvdb_id("Our House") == 2
