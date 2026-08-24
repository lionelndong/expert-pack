import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("build_brain.py")
SPEC = importlib.util.spec_from_file_location("hormozi_build", MODULE_PATH)
assert SPEC and SPEC.loader
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def test_integrate_ocr_promotes_canonical_coverage_after_review(tmp_path):
    ocr_root = tmp_path / "ocr-results"
    (ocr_root / "reports").mkdir(parents=True)
    (ocr_root / "atoms").mkdir()
    (ocr_root / "manual-review").mkdir()
    report = {
        "source_id": "src-test",
        "pages_requested": [1],
        "pages_completed": 1,
        "low_confidence_pages": [1],
        "results": [{
            "source_id": "src-test",
            "page": 1,
            "classification": "low_confidence_manual_review",
            "manual_review_required": True,
        }],
    }
    (ocr_root / "reports" / "src-test.json").write_text(json.dumps(report), encoding="utf-8")
    (ocr_root / "atoms" / "src-test-page-0001.md").write_text(
        "---\nsource_id: src-test\nsource_page: 1\n---\n# OCR page\n",
        encoding="utf-8",
    )
    (ocr_root / "manual-review" / "manual-review-manifest.json").write_text(json.dumps({
        "visual_qa_status": "manual_review_complete",
        "page_count": 1,
        "reviewed_count": 1,
        "pending_count": 0,
        "decision_counts": {"accept_ocr": 1, "graphic_or_blank": 0, "reocr_required": 0, "unreadable": 0},
    }), encoding="utf-8")
    result = BUILD.integrate_ocr(tmp_path / "pack", ocr_root, [])
    assert result["status"] == "indexed_ocr_recovered_manual_visual_qa_complete"
    assert result["visual_qa_status"] == "manual_review_complete"
    assert result["manual_review_pages"] == 1
    assert (tmp_path / "pack" / "ocr" / "src-test-page-0001.md").is_file()


def test_coverage_categories_are_explicit_and_do_not_hide_pending_sources(tmp_path):
    meta = tmp_path / "meta"
    meta.mkdir()
    (tmp_path / "present.md").write_text("present", encoding="utf-8")
    (meta / "official-channel-catalog.json").write_text(json.dumps({
        "channels": [{"videos": [{
            "video_id": "captionless1",
            "title": "Captionless",
            "channel_url": "https://www.youtube.com/@AlexHormozi",
            "status": "caption_unavailable_pending_openai_transcription",
        }]}],
    }), encoding="utf-8")
    ledger = [
        {"kind": "inventory_record", "record_id": "included", "status": "indexed_evidence", "absolute_path": str(tmp_path / "present.md")},
        {"kind": "inventory_record", "record_id": "duplicate", "status": "duplicate_by_sha256", "absolute_path": str(tmp_path / "present.md")},
        {"kind": "inventory_record", "record_id": "incomplete", "status": "approved_but_format_pending", "absolute_path": str(tmp_path / "present.md")},
        {"kind": "inventory_record", "record_id": "unsupported", "status": "excluded_by_rights_or_scope", "absolute_path": str(tmp_path / "present.md")},
        {"kind": "inventory_record", "record_id": "quarantine", "status": "quarantined_restricted_authorization_required", "absolute_path": str(tmp_path / "present.md")},
        {"kind": "inventory_record", "record_id": "missing", "status": "indexed_evidence", "absolute_path": str(tmp_path / "absent.md")},
    ]
    report = BUILD.coverage_categories(tmp_path, ledger, {
        "audio": [{"path": "audio.mp3", "status": "metadata_ready_pending_transcription"}],
    })
    assert report["categories"] == {
        "included": 2,
        "duplicate": 1,
        "incomplete": 1,
        "unsupported": 1,
        "quarantined": 1,
        "missing": 1,
    }
    assert len(report["missing"]["official_captionless_videos"]) == 1
    assert len(report["missing"]["audio_pending_transcription"]) == 1
    assert report["missing"]["inventory_records"][0]["record_id"] == "missing"
