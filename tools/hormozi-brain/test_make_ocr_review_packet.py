import importlib.util
import json
from pathlib import Path

from PIL import Image


MODULE_PATH = Path(__file__).with_name("make_ocr_review_packet.py")
SPEC = importlib.util.spec_from_file_location("hormozi_review_packet", MODULE_PATH)
assert SPEC and SPEC.loader
PACKET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKET)


def test_packet_keeps_low_confidence_pages_pending(tmp_path):
    root = tmp_path / "ocr"
    (root / "reports").mkdir(parents=True)
    (root / "atoms").mkdir()
    image_path = root / "page-0001.png"
    Image.new("RGB", (40, 40), "white").save(image_path)
    (root / "reports" / "src-test.json").write_text(json.dumps({
        "source_id": "src-test",
        "relative_path": "book.pdf",
        "results": [{
            "page": 1,
            "classification": "low_confidence_manual_review",
            "mean_confidence": 42.0,
            "min_confidence": 0.0,
            "word_count": 3,
            "qa_image": str(image_path),
            "manual_review_required": True,
        }],
    }), encoding="utf-8")
    manifest = PACKET.build_packet(root, tmp_path / "packet")
    assert manifest["visual_qa_status"] == "pending_manual_review"
    assert manifest["page_count"] == 1
    assert manifest["pages"][0]["review_status"] == "pending"
    assert manifest["pages"][0]["review_decision"] is None

    manifest_path = tmp_path / "packet" / "manual-review-manifest.json"
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    persisted["pages"][0].update({
        "review_status": "reviewed",
        "review_decision": "accept_ocr",
        "review_notes": "preserve this decision",
        "reviewed_at_utc": "2026-01-01T00:00:00+00:00",
    })
    manifest_path.write_text(json.dumps(persisted), encoding="utf-8")
    rebuilt = PACKET.build_packet(root, tmp_path / "packet")
    assert rebuilt["visual_qa_status"] == "manual_review_complete"
    assert rebuilt["pages"][0]["review_decision"] == "accept_ocr"

    # Once the durable review sheet exists, individual page renders can be
    # cleaned up without reopening a completed QA gate.
    image_path.unlink()
    reused = PACKET.build_packet(root, tmp_path / "packet", reuse_complete=True)
    assert reused["visual_qa_status"] == "manual_review_complete"
    assert reused["sheets"]
