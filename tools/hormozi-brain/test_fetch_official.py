from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("fetch_official.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("hormozi_fetch_official", MODULE_PATH)
assert SPEC and SPEC.loader
FETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FETCH)


def test_refresh_report_records_metadata_only_provenance(tmp_path, monkeypatch):
    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "channel_id": "UC-official",
                "channel": "Alex Hormozi",
                "entries": [
                    {
                        "id": "ABCDEFGHIJK",
                        "title": "Example",
                        "webpage_url": "https://www.youtube.com/watch?v=ABCDEFGHIJK",
                    }
                ],
            }

    monkeypatch.setattr(FETCH, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fetch_official.py",
            "--channel-url",
            "https://www.youtube.com/@AlexHormozi/videos",
            "--output",
            str(tmp_path),
        ],
    )

    assert FETCH.main() == 0
    report = json.loads((tmp_path / "meta" / "official-channel-catalog.json").read_text(encoding="utf-8"))
    assert report["report_version"] == "1.1"
    assert report["verification_method"].startswith("yt-dlp metadata-only")
    assert report["media_downloaded"] is False
    assert report["captions_requested"] is False
    assert report["verified_channel_urls"] == ["https://www.youtube.com/@AlexHormozi/videos"]
    assert report["channels"][0]["channel"] == "Alex Hormozi"
    assert report["channels"][0]["videos"][0]["status"] == "caption_unavailable_pending_openai_transcription"


def test_caption_selection_checks_automatic_english_tracks():
    track = FETCH.caption_track(
        {
            "subtitles": {"fr": [{"url": "manual-fr", "ext": "vtt"}]},
            "automatic_captions": {"en": [{"url": "auto-en", "ext": "json3"}]},
        }
    )
    assert track == ("auto-en", "json3", "automatic")
    assert FETCH.caption_url(
        {
            "subtitles": {"fr": [{"url": "manual-fr", "ext": "vtt"}]},
            "automatic_captions": {"en": [{"url": "auto-en", "ext": "json3"}]},
        }
    ) == "auto-en"


def test_caption_selection_accepts_all_supported_youtube_formats():
    track = FETCH.caption_track(
        {
            "subtitles": {"en": [{"url": "manual-en", "ext": "ttml"}]},
            "automatic_captions": {},
        }
    )
    assert track == ("manual-en", "ttml", "manual")


def test_caption_parsers_support_srv3_and_json3():
    assert FETCH.parse_caption('<transcript><text start="0">Hello &amp; welcome</text><text start="1">Hello &amp; welcome</text></transcript>') == "Hello & welcome"
    assert FETCH.parse_caption(json.dumps({"events": [{"segs": [{"utf8": "Offer "}, {"utf8": "more value."}]}]})) == "Offer more value."


def test_caption_segments_preserve_vtt_xml_and_json_timestamps():
    vtt = "WEBVTT\n\n00:00:01.500 --> 00:00:03.000\nAdd value.\n"
    ttml = '<tt:p xmlns:tt="http://www.w3.org/ns/ttml" begin="00:00:04.250">Price it right.</tt:p>'
    json3 = json.dumps({"events": [{"tStartMs": 6500, "segs": [{"utf8": "Close."}]}]})
    assert FETCH.parse_caption_segments(vtt) == [(1.5, "Add value.")]
    assert FETCH.parse_caption_segments(ttml) == [(4.25, "Price it right.")]
    assert FETCH.parse_caption_segments(json3) == [(6.5, "Close.")]
    assert FETCH.format_caption_segments(FETCH.parse_caption_segments(vtt)) == "(0:01) Add value."


def test_empty_caption_track_remains_explicitly_pending(tmp_path, monkeypatch):
    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {"subtitles": {"en": [{"url": "caption", "ext": "vtt"}]}}

    monkeypatch.setattr(FETCH, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(FETCH, "fetch_caption", lambda _url: "")
    video_id, row = FETCH.fetch_missing_caption(
        {"video_id": "ABCDEFGHIJK", "title": "Example", "url": "https://youtu.be/ABCDEFGHIJK"},
        "https://www.youtube.com/@AlexHormozi/videos",
        tmp_path,
        {},
    )
    assert video_id == "ABCDEFGHIJK"
    assert row["status"] == "caption_unavailable_pending_openai_transcription"
    assert row["caption_resolution"] == "caption_empty"


def test_caption_fetch_error_remains_explicitly_pending(tmp_path, monkeypatch):
    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {"subtitles": {"en": [{"url": "caption", "ext": "vtt"}]}}

    monkeypatch.setattr(FETCH, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(FETCH, "fetch_caption", lambda _url: (_ for _ in ()).throw(RuntimeError("network")))
    _video_id, row = FETCH.fetch_missing_caption(
        {"video_id": "ABCDEFGHIJK", "title": "Example", "url": "https://youtu.be/ABCDEFGHIJK"},
        "https://www.youtube.com/@AlexHormozi/videos",
        tmp_path,
        {},
    )
    assert row["status"] == "caption_unavailable_pending_openai_transcription"
    assert row["caption_resolution"] == "caption_error"
    assert row["caption_error"] == "RuntimeError"
