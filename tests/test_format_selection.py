"""Deterministic reproduction of the 480p bug and proof of the fix, using yt-dlp's real
format selector against a synthetic modern-YouTube format table (avc1/mp4 only up to 480p,
HD only as vp9/webm — the common case that broke production)."""
import yt_dlp

from streamarr.providers.youtube import format_opts, format_string

Q = {"max_resolution": 1080, "max_fps": 60, "video_format": "mp4",
     "audio_codec": "aac", "audio_format": "m4a"}


def _f(**kw):
    kw.setdefault("url", "https://example.invalid/f")
    kw.setdefault("protocol", "https")
    return kw


VP9_ONLY_HD = [
    _f(format_id="18", ext="mp4", width=640, height=360, fps=30,
       vcodec="avc1.42001E", acodec="mp4a.40.2", tbr=500),
    _f(format_id="135", ext="mp4", width=854, height=480, fps=30,
       vcodec="avc1.4d401e", acodec="none", tbr=800),
    _f(format_id="248", ext="webm", width=1920, height=1080, fps=30,
       vcodec="vp9", acodec="none", tbr=2500),
    _f(format_id="140", ext="m4a", vcodec="none", acodec="mp4a.40.2", abr=129, tbr=129),
]


def _pick(fmt, sort=None, formats=VP9_ONLY_HD):
    opts = {"format": fmt, "quiet": True, "simulate": True}
    if sort:
        opts["format_sort"] = sort
    ydl = yt_dlp.YoutubeDL(opts)
    info = {"id": "t", "title": "t", "formats": [dict(f) for f in formats],
            "webpage_url": "https://example.invalid",
            "extractor": "youtube", "extractor_key": "Youtube"}
    r = ydl.process_video_result(info, download=False)
    rf = r.get("requested_formats") or [r]
    return rf


def test_old_filter_chain_documented_bug():
    picked = _pick(format_string(Q, "video"))
    assert picked[0].get("height") == 480  # ext=mp4 hard filter capped at 480p


def test_new_sort_based_selection_gets_1080p():
    fmt, sort = format_opts(Q, "video")
    picked = _pick(fmt, sort)
    assert picked[0].get("height") == 1080
    assert picked[0].get("vcodec", "").startswith("vp9")


def test_resolution_cap_still_respected():
    fmt, sort = format_opts(dict(Q, max_resolution=480), "video")
    picked = _pick(fmt, sort)
    assert picked[0].get("height") == 480


def test_audio_sort():
    fmt, sort = format_opts(dict(Q, audio_format="m4b"), "audio")
    assert fmt == "ba/b" and "aext:m4a" in sort
