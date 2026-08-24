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
