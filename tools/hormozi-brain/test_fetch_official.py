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
