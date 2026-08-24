import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("review_ocr_packet.py")
SPEC = importlib.util.spec_from_file_location("hormozi_review", MODULE_PATH)
assert SPEC and SPEC.loader
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


def _manifest():
    return {
        "page_count": 2,
        "pages": [
            {"review_id": "src-a:page-0001", "review_status": "pending", "review_decision": None},
            {"review_id": "src-a:page-0002", "review_status": "pending", "review_decision": None},
        ],
    }


def test_decisions_keep_packet_pending_until_every_page_is_reviewed():
    manifest = _manifest()
    summary = REVIEW.record_decision(manifest, "src-a:page-0001", "accept_ocr", "legible")
    assert summary["visual_qa_status"] == "pending_manual_review"
    assert summary["reviewed_count"] == 1
    summary = REVIEW.record_decision(manifest, "src-a:page-0002", "graphic_or_blank")
    assert summary["visual_qa_status"] == "manual_review_complete"
    assert summary["pending_count"] == 0
    assert manifest["pages"][1]["reviewed_at_utc"]


def test_invalid_or_changed_decisions_fail_closed():
    manifest = _manifest()
    with pytest.raises(ValueError, match="Invalid decision"):
        REVIEW.record_decision(manifest, "src-a:page-0001", "guess")
    REVIEW.record_decision(manifest, "src-a:page-0001", "accept_ocr")
    with pytest.raises(ValueError, match="allow-change"):
        REVIEW.record_decision(manifest, "src-a:page-0001", "reocr_required")
    REVIEW.record_decision(manifest, "src-a:page-0001", "reocr_required", allow_change=True)
    assert manifest["pages"][0]["review_decision"] == "reocr_required"


def test_validate_rejects_duplicate_page_ids():
    manifest = _manifest()
    manifest["pages"][1]["review_id"] = manifest["pages"][0]["review_id"]
    with pytest.raises(ValueError, match="Duplicate"):
        REVIEW.validate_manifest(manifest)
