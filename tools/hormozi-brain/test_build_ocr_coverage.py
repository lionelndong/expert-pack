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
        "---\nsource_id: src-test\nsource_page: 1\ncontent_hash: sha256:stale\nconfidence: manually_transcribed\n---\n# OCR page\n",
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
    output = tmp_path / "pack" / "ocr" / "src-test-page-0001.md"
    assert output.is_file()
    normalized = output.read_text(encoding="utf-8")
    assert "confidence: expert-verified" in normalized
    assert "content_hash: sha256:stale" not in normalized


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


def test_restricted_handoff_is_fail_closed_without_resolution_report(tmp_path):
    manifest = {
        "quarantined_sources": [
            {"source_id": source_id, "relative_path": f"{source_id}.pdf", "path": str(tmp_path / f"{source_id}.pdf")}
            for source_id in BUILD.RESTRICTED_SOURCE_IDS
        ]
    }
    ledger = [
        {"kind": "inventory_record", "record_id": source_id, "status": "quarantined_restricted_authorization_required"}
        for source_id in BUILD.RESTRICTED_SOURCE_IDS
    ]
    result = BUILD.integrate_restricted(tmp_path / "pack", manifest, ledger, tmp_path / "missing-resolution.json")
    assert result["status"] == "pending_external_authorization"
    assert all(row["status"] == "quarantined_restricted_authorization_required" for row in ledger)
    report = json.loads((tmp_path / "missing-resolution.json").read_text(encoding="utf-8"))
    assert report["status"] == "pending_external_authorization"
    assert report["authorization_required"] is True
    assert report["sources"][0]["processing_status"] == "not_opened"
    assert "sha256" not in json.dumps(report)


def test_authorized_restricted_handoff_hashes_deduplicates_and_ingests_ocr(tmp_path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"authorized pricing playbook")
    second.write_bytes(first.read_bytes())
    source_ids = sorted(BUILD.RESTRICTED_SOURCE_IDS)
    manifest = {
        "quarantined_sources": [
            {"source_id": source_ids[0], "relative_path": "first.pdf", "path": str(first)},
            {"source_id": source_ids[1], "relative_path": "second.pdf", "path": str(second)},
        ]
    }
    report_dir = tmp_path / "restricted" / source_ids[0]
    atoms_dir = report_dir / "atoms"
    atoms_dir.mkdir(parents=True)
    (atoms_dir / "page-0001.md").write_text(
        "---\nsource_id: " + source_ids[0] + "\nsource_page: 1\n---\n# Pricing\n", encoding="utf-8"
    )
    report_path = report_dir / "ocr-report.json"
    report_path.write_text("{}", encoding="utf-8")
    resolution = {
        "status": "authorized_processed",
        "sources": [
            {"source_id": source_ids[0], "path": str(first), "sha256": BUILD.sha256_file(first)},
            {"source_id": source_ids[1], "path": str(second), "sha256": BUILD.sha256_file(second)},
        ],
        "duplicate_groups": [{"sha256": BUILD.sha256_file(first), "source_ids": source_ids, "representative_source_id": source_ids[0], "duplicate": True}],
        "unique_work_count": 1,
        "ocr_requested": True,
        "ocr": [{"source_id": source_ids[0], "report": str(report_path)}],
    }
    resolution_path = tmp_path / "restricted-source-resolution.json"
    resolution_path.write_text(json.dumps(resolution), encoding="utf-8")
    ledger = [
        {"kind": "inventory_record", "record_id": source_ids[0], "status": "quarantined_restricted_authorization_required", "pack_membership": []},
        {"kind": "inventory_record", "record_id": source_ids[1], "status": "quarantined_restricted_authorization_required", "pack_membership": []},
    ]
    result = BUILD.integrate_restricted(tmp_path / "pack", manifest, ledger, resolution_path)
    assert result["status"] == "authorized_processed"
    assert result["unique_work_count"] == 1
    assert result["ocr_atoms"] == 1
    records = {row["record_id"]: row for row in ledger if row["kind"] == "inventory_record"}
    assert records[source_ids[0]]["status"] == "indexed_restricted_ocr"
    assert records[source_ids[1]]["status"] == "duplicate_by_sha256"
    assert records[source_ids[1]]["duplicate_of"] == source_ids[0]
    assert (tmp_path / "pack" / "ocr" / f"restricted-{source_ids[0]}-page-0001.md").is_file()


def test_restricted_handoff_rejects_inconsistent_duplicate_hash(tmp_path):
    source_ids = sorted(BUILD.RESTRICTED_SOURCE_IDS)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    manifest = {"quarantined_sources": [
        {"source_id": source_ids[0], "relative_path": "first.pdf", "path": str(first)},
        {"source_id": source_ids[1], "relative_path": "second.pdf", "path": str(second)},
    ]}
    resolution = {
        "status": "authorized_processed",
        "sources": [
            {"source_id": source_ids[0], "path": str(first), "sha256": BUILD.sha256_file(first)},
            {"source_id": source_ids[1], "path": str(second), "sha256": BUILD.sha256_file(second)},
        ],
        "duplicate_groups": [{"sha256": BUILD.sha256_file(first), "source_ids": source_ids, "representative_source_id": source_ids[0], "duplicate": True}],
        "unique_work_count": 1,
        "ocr_requested": False,
        "ocr": [],
    }
    resolution_path = tmp_path / "resolution.json"
    resolution_path.write_text(json.dumps(resolution), encoding="utf-8")
    ledger = [{"kind": "inventory_record", "record_id": source_id, "status": "quarantined_restricted_authorization_required"} for source_id in source_ids]
    result = BUILD.integrate_restricted(tmp_path / "pack", manifest, ledger, resolution_path)
    assert result["status"] == "invalid_resolution_report"
    assert all(row["status"] == "quarantined_restricted_authorization_required" for row in ledger)


def test_ingest_audio_transcription_preserves_timestamped_provenance(tmp_path):
    audio = tmp_path / "Money Models.mp3"
    audio.write_bytes(b"approved audio placeholder")
    transcription_root = tmp_path / "transcriptions"
    transcription_root.mkdir()
    (transcription_root / "money-models.json").write_text(json.dumps({
        "status": "transcribed",
        "audio": str(audio),
        "model": "gpt-4o-mini-transcribe",
        "transcribed_at": "2026-08-23T00:00:00+00:00",
        "duration_seconds": 125,
        "segments": [
            {"start": 0, "end": 3, "text": "Make the offer clear."},
            {"start": 62.4, "end": 65, "text": "Then measure the result."},
        ],
    }), encoding="utf-8")
    ledger = [{"record_id": "derived-audio-money-models", "status": "metadata_ready_pending_transcription"}]
    audio_row = {"source_id": "audio-source", "path": str(audio), "relative_path": "library/Money Models.mp3", "hash": "sha256:audio"}
    result = BUILD.ingest_audio_transcriptions(
        transcription_root,
        tmp_path / "pack",
        ledger,
        [audio_row],
    )
    assert result["status"] == "complete"
    assert result["transcribed"] == 1
    assert audio_row["status"] == "included_audio_transcript"
    assert ledger[0]["status"] == "included_audio_transcript"
    output = tmp_path / "pack" / "audio" / "money-models-transcript.md"
    text = output.read_text(encoding="utf-8")
    assert "[00:01:02] Then measure the result." in text
    assert "sha256:audio" in text
    assert any(row.get("kind") == "derived_audio_transcript" for row in ledger)
