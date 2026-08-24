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
