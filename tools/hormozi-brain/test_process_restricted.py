import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).with_name("process_restricted.py")
SPEC = importlib.util.spec_from_file_location("hormozi_restricted", MODULE_PATH)
assert SPEC and SPEC.loader
RESTRICTED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESTRICTED)


IDS = sorted(RESTRICTED.RESTRICTED_IDS)


def authorization_record():
    return {
        "authorization_status": "authorized",
        "permitted_internal_processing": True,
        "sources": [
            {
                "source_id": source_id,
                "rights_owner": "Company legal",
                "department": "Knowledge Systems",
                "obtained_via": "documented internal transfer",
                "scope": "internal retrieval and OCR",
                "approver": "owner@example.test",
                "approved_at": "2026-08-23",
                "ticket": "LEGAL-123",
            }
            for source_id in IDS
        ],
    }


def test_unauthorized_processing_refuses_before_hash(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"quarantined_sources": [{"source_id": source_id, "path": str(tmp_path / "missing.pdf")} for source_id in IDS]}), encoding="utf-8")
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps({"authorization_status": "pending", "sources": []}), encoding="utf-8")
    with pytest.raises(PermissionError, match="refused"):
        RESTRICTED.process(manifest, authorization, tmp_path / "out", False)


def test_authorized_processing_hashes_and_deduplicates(tmp_path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"same authorized bytes")
    second.write_bytes(b"same authorized bytes")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"quarantined_sources": [{"source_id": IDS[0], "path": str(first)}, {"source_id": IDS[1], "path": str(second)}]}), encoding="utf-8")
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps(authorization_record()), encoding="utf-8")
    report = RESTRICTED.process(manifest, authorization, tmp_path / "out", False)
    assert report["status"] == "authorized_processed"
    assert report["unique_work_count"] == 1
    assert report["duplicate_groups"][0]["duplicate"] is True
    assert report["ocr_requested"] is False


def test_authorization_rejects_extra_or_duplicate_source_entries():
    record = authorization_record()
    record["sources"].append(dict(record["sources"][0]))
    errors = RESTRICTED.validate_authorization(record)
    assert any("duplicate authorization entries" in error for error in errors)
    assert any("exactly 2 source entries" in error for error in errors)


def test_authorization_rejects_unknown_source_id():
    record = authorization_record()
    record["sources"][0]["source_id"] = "unrelated-source"
    errors = RESTRICTED.validate_authorization(record)
    assert any("unknown sources" in error for error in errors)
    assert any(f"missing authorization for {IDS[0]}" in error for error in errors)


def test_authorization_rejects_unfilled_template_placeholders():
    record = authorization_record()
    record["sources"][0]["rights_owner"] = "REPLACE_WITH_NAMED_RIGHTS_OWNER"
    errors = RESTRICTED.validate_authorization(record)
    assert any("template placeholders" in error for error in errors)
