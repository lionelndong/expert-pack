import importlib.util
import json
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path(__file__).with_name("transcribe_audio.py")
SPEC = importlib.util.spec_from_file_location("hormozi_transcribe_audio", MODULE_PATH)
assert SPEC and SPEC.loader
AUDIO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIO)


def test_plan_chunks_covers_duration_without_overlap():
    chunks = AUDIO.plan_chunks(13092.7, 600)
    assert chunks[0] == (0, 600)
    assert chunks[-1][1] == 13093
    assert all(left[1] == right[0] for left, right in zip(chunks, chunks[1:]))
    assert len(chunks) == 22


def test_missing_key_fails_before_upload(tmp_path, monkeypatch):
    audio = tmp_path / "dummy.mp3"
    audio.write_bytes(b"not audio")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(AUDIO, "duration_seconds", lambda _: 1.0)
    monkeypatch.setattr(sys, "argv", ["transcribe_audio.py", "--audio", str(audio), "--output", str(tmp_path / "out.json")])
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        AUDIO.main()


def test_estimate_only_reports_usage_without_key(tmp_path, monkeypatch):
    audio = tmp_path / "dummy.mp3"
    audio.write_bytes(b"not audio")
    output = tmp_path / "estimate.json"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(AUDIO, "duration_seconds", lambda _: 120.0)
    monkeypatch.setattr(sys, "argv", ["transcribe_audio.py", "--audio", str(audio), "--output", str(output), "--estimate-only", "--price-per-minute-usd", "0.01"])
    assert AUDIO.main() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["estimated_audio_minutes"] == 2.0
    assert report["estimated_chunks"] == 1
    assert report["projected_transcription_cost_usd"] == 0.02
