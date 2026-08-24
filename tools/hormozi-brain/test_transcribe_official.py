import importlib.util
import json
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path(__file__).with_name("transcribe_official.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("hormozi_transcribe_official", MODULE_PATH)
assert SPEC and SPEC.loader
OFFICIAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OFFICIAL)


def test_segment_lines_preserves_timestamp_locators():
    assert OFFICIAL.segment_lines([{"start": 61.2, "text": "Offer more value."}], "fallback") == "[00:01:01] Offer more value."


def test_missing_key_fails_before_media_download(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"channels": [{"videos": [{"video_id": "ABCDEFGHIJK", "url": "https://youtu.be/ABCDEFGHIJK"}]}]}),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["transcribe_official.py", "--video-id", "ABCDEFGHIJK", "--catalog", str(catalog), "--output", str(tmp_path)])
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        OFFICIAL.main()


def test_metadata_estimate_does_not_download_media(monkeypatch):
    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {"duration": 125.0, "title": "Example"}

    monkeypatch.setattr(OFFICIAL, "YoutubeDL", FakeYDL)
    estimate = OFFICIAL.estimate_video({"video_id": "ABCDEFGHIJK", "url": "https://youtu.be/ABCDEFGHIJK"}, "test-model", 60, 0.01)
    assert estimate["estimated_chunks"] == 3
    assert estimate["media_downloaded"] is False
    assert estimate["api_called"] is False
    assert estimate["projected_transcription_cost_usd"] == round(125 / 60 * 0.01, 6)


def test_unavailable_estimate_is_explicit_and_fail_closed():
    estimate = OFFICIAL.unavailable_estimate(
        {"video_id": "ABCDEFGHIJK", "title": "Example", "url": "https://youtu.be/ABCDEFGHIJK"},
        "test-model",
        600,
        None,
        RuntimeError("bot challenge"),
    )
    assert estimate["metadata_status"] == "unavailable_pending_transcription"
    assert estimate["duration_seconds"] is None
    assert estimate["estimated_chunks"] is None
    assert estimate["media_downloaded"] is False
    assert estimate["api_called"] is False


def test_metadata_cache_estimate_does_not_download_media():
    estimate = OFFICIAL.estimate_from_metadata(
        {"video_id": "ABCDEFGHIJK", "title": "Fallback", "url": "https://youtu.be/ABCDEFGHIJK"},
        {
            "video_id": "ABCDEFGHIJK",
            "title": "Cached title",
            "url": "https://www.youtube.com/watch?v=ABCDEFGHIJK",
            "duration_seconds": 426,
            "duration_iso": "PT7M6S",
            "metadata_source": "youtube_page_meta[itemprop=duration]",
        },
        "test-model",
        600,
        0.01,
    )
    assert estimate["title"] == "Cached title"
    assert estimate["estimated_chunks"] == 1
    assert estimate["metadata_status"] == "available"
    assert estimate["media_downloaded"] is False
    assert estimate["api_called"] is False
