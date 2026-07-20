import re
from datetime import datetime, timezone

SXXEYY = [
    re.compile(r"\bS(\d{1,2})[ ./]?E(\d{1,3})\b", re.I),
    re.compile(r"\(S(\d{1,2})/E(\d{1,3})\)", re.I),
    re.compile(r"\bStaffel\s*(\d{1,2})\D{0,10}Folge\s*(\d{1,3})", re.I),
    re.compile(r"\bFolge\s*(\d{1,3})\b", re.I),
]


def clean(text):
    text = re.sub(r"\s*[\\/]\s*", "-", text or "")   # "(1/3)" -> "(1-3)", not "(13)"
    text = re.sub(r"[:*?\"<>|]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_sxxeyy(title):
    for i, rx in enumerate(SXXEYY):
        m = rx.search(title or "")
        if m:
            if len(m.groups()) == 2:
                return int(m.group(1)), int(m.group(2))
            return 1, int(m.group(1))
    return None


GROUP = "-Streamarr"  # trailing release group helps arr parsers recognise the release


def release_title(item, scheme, quality_tag="WEBDL-1080p"):
    """Build an arr-parsable release name for a cached item dict."""
    series = clean(item.get("series_title") or item.get("meta", {}).get("channel") or "Unknown")
    title = clean(item["title"])
    date = None
    if item.get("published"):
        date = datetime.fromtimestamp(item["published"], tz=timezone.utc).strftime("%Y-%m-%d")

    if scheme == "arr":
        if item.get("_arr_se"):
            s, e = item["_arr_se"]
            # source titles often embed their own numbering ("(S01-E02)") — strip it so the
            # arr parser can't pick up the wrong episode identity
            safe = re.sub(r"\(?\bS\d{1,4}\s*[-. ]?\s*E\d{1,3}\b\)?", "", title, flags=re.I)
            safe = re.sub(r"\s+", " ", safe).strip(" -–")
            return f"{series} - S{s:02d}E{e:03d} - {safe} [{quality_tag}]{GROUP}"
        scheme = "auto"
    if scheme in ("auto", "sxxeyy"):
        se = parse_sxxeyy(title)
        if se:
            s, e = se
            return f"{series} - S{s:02d}E{e:03d} - {title} [{quality_tag}]{GROUP}"
        scheme = "date"  # sxxeyy items without a parseable tag are filtered out at search time
    if scheme == "absolute" and item.get("ordinal"):
        return f"{series} - S01E{item['ordinal']:03d} - {title} [{quality_tag}]{GROUP}"
    if date:
        return f"{series} - {date} - {title} [{quality_tag}]{GROUP}"
    return f"{series} - {title} [{quality_tag}]{GROUP}"


def quality_tag(cfg_quality, media):
    if media == "audio":
        return cfg_quality.get("audio_codec", "aac").upper()
    return f"WEBDL-{cfg_quality.get('max_resolution', 1080)}p"
