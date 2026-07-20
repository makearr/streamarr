from streamarr import naming


def test_parse_sxxeyy_variants():
    assert naming.parse_sxxeyy("Show S02E05 something") == (2, 5)
    assert naming.parse_sxxeyy("Krimi (S01/E13)") == (1, 13)
    assert naming.parse_sxxeyy("Staffel 3 Folge 7") == (3, 7)
    assert naming.parse_sxxeyy("Folge 21") == (1, 21)
    assert naming.parse_sxxeyy("no episode info") is None


def test_release_title_absolute():
    item = {"series_title": "Kanal", "title": "Video", "ordinal": 4, "published": None, "meta": {}}
    assert naming.release_title(item, "absolute", "WEBDL-1080p") == "Kanal - S01E004 - Video [WEBDL-1080p]-Streamarr"


def test_release_title_date():
    item = {"series_title": "Doku", "title": "Thema", "ordinal": None, "published": 1752000000, "meta": {}}
    assert naming.release_title(item, "date").startswith("Doku - 2025-07-08 - Thema")


def test_release_title_auto_prefers_sxxeyy():
    item = {"series_title": "Serie", "title": "S01E02 Pilot", "published": 1752000000, "meta": {}}
    assert "S01E002" in naming.release_title(item, "auto")


def test_clean_strips_forbidden_chars():
    assert naming.clean('a/b\\c:d*e?"f<g>h|i') == "a-b-cdefghi"
