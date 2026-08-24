import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).with_name("transcribe_official.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("hormozi_transcribe_official", MODULE_PATH)
assert SPEC and SPEC.loader
OFFICIAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OFFICIAL)


def test_segment_lines_preserves_timestamp_locators():
    assert OFFICIAL.segment_lines([{"start": 61.2, "text": "Offer more value."}], "fallback") == "[00:01:01] Offer more value."


def test_catalog_videos_inherits_verified_channel_provenance():
    videos = OFFICIAL.catalog_videos(
        {
            "channels": [
                {
                    "channel_url": "https://www.youtube.com/@AlexHormozi/videos",
                    "channel_id": "UC-official",
                    "channel": "Alex Hormozi",
                    "videos": [{"video_id": "ABCDEFGHIJK", "url": "https://youtu.be/ABCDEFGHIJK"}],
                }
            ]
        }
    )
    assert videos["ABCDEFGHIJK"]["channel_url"] == "https://www.youtube.com/@AlexHormozi/videos"
    assert videos["ABCDEFGHIJK"]["channel_id"] == "UC-official"
    assert videos["ABCDEFGHIJK"]["channel"] == "Alex Hormozi"


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


def test_transcribe_video_returns_catalog_update_after_success(tmp_path, monkeypatch):
    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "title": "Example",
                "channel_url": "https://youtube.com/@AlexHormozi",
                "channel_id": "UC-official",
                "channel": "Alex Hormozi",
                "upload_date": "20260102",
            }

        def download(self, _urls):
            output_template = str(self.options["outtmpl"])
            Path(output_template.replace("%(id)s", "ABCDEFGHIJK").replace("%(ext)s", "m4a")).write_bytes(b"temporary audio")

    class FakeOpenAI:
        pass

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(OFFICIAL, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(OFFICIAL.shutil, "which", lambda _command: "fake-tool")
    monkeypatch.setattr(OFFICIAL, "duration_seconds", lambda _path: 61.0)
    monkeypatch.setattr(OFFICIAL, "transcribe_chunk", lambda *_args, **_kwargs: {
        "text": "Offer more value.",
        "segments": [{"start": 0.0, "end": 1.5, "text": "Offer more value."}],
    })
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=lambda: FakeOpenAI()))

    def fake_run(*_args, **_kwargs):
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    result = OFFICIAL.transcribe_video(
        {
            "video_id": "ABCDEFGHIJK",
            "url": "https://youtu.be/ABCDEFGHIJK",
            "title": "Fallback",
            "channel_url": "https://www.youtube.com/@AlexHormozi/videos",
            "channel_id": "UC-official",
            "channel": "Alex Hormozi",
        },
        tmp_path,
        "test-model",
        600,
        1,
    )
    assert result["status"] == "openai_transcribed"
    assert result["audio_retained"] is False
    assert result["channel"] == "Alex Hormozi"
    assert result["channel_id"] == "UC-official"
    assert result["channel_url"] == "https://www.youtube.com/@AlexHormozi/videos"
    assert result["published_at"] == "20260102"
    assert Path(result["transcript_path"]).is_file()
    transcript = Path(result["transcript_path"]).read_text(encoding="utf-8")
    assert "- Channel: Alex Hormozi" in transcript
    assert "- Publication metadata: `20260102`" in transcript
