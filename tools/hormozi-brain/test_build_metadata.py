from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools/hormozi-brain/build_brain.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("hormozi_build_brain", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_metadata_uses_current_build_date():
    builder = load_builder()
    expected = datetime.now(timezone.utc).date().isoformat()

    assert builder.BUILD_DATE == expected
    atom = builder.transcript_markdown(
        {
            "title": "Example",
            "url": "https://www.youtube.com/watch?v=example",
            "video_id": "example",
            "transcript": "(0:01) Evidence",
            "source_file": "example.txt",
            "source_line_start": 1,
            "source_line_end": 2,
            "part_index": 1,
            "part_count": 1,
            "start_timestamp": 1,
            "end_timestamp": 1,
        }
    )

    assert f"verified_at: '{expected}'" in atom
