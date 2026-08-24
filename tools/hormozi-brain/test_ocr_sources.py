import csv
import importlib.util
import zipfile
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("ocr_sources.py")
SPEC = importlib.util.spec_from_file_location("hormozi_ocr_sources", MODULE_PATH)
assert SPEC and SPEC.loader
OCR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OCR)

BUILDER_SPEC = importlib.util.spec_from_file_location("hormozi_brain_builder", Path(__file__).with_name("build_brain.py"))
assert BUILDER_SPEC and BUILDER_SPEC.loader
BUILDER = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(BUILDER)


def test_parse_tsv_preserves_lines_and_confidence(tmp_path):
    tsv = tmp_path / "page.tsv"
    tsv.write_text(
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t92.5\tOffer\n"
        "5\t1\t1\t1\t1\t2\t88.0\tvalue\n"
        "5\t1\t1\t1\t2\t1\t70.0\tProof\n",
        encoding="utf-8",
    )
    text, mean, minimum, words = OCR.parse_tsv(tsv)
    assert text == "Offer value\nProof"
    assert mean == (92.5 + 88.0 + 70.0) / 3
    assert minimum == 70.0
    assert words == 3


def test_atom_markdown_has_page_locator_and_hash():
    atom = OCR.atom_markdown(
        title="Offers — OCR page 4",
        source_id="src-example",
        pdf=Path("offers.pdf"),
        relative_path="Books/offers.pdf",
        page=4,
        text="Offer value",
        mean_confidence=91.0,
        min_confidence=80.0,
        word_count=2,
    )
    assert "id: alex-hormozi-brain/ocr/src-example/page-0004" in atom
    assert "- Page: 4" in atom
    assert "content_hash:" in atom
    assert "Offer value" in atom


def test_integrate_ocr_updates_inventory_and_copies_nonblank_atom(tmp_path):
    ocr_root = tmp_path / "ocr-results"
    (ocr_root / "atoms").mkdir(parents=True)
    (ocr_root / "reports").mkdir(parents=True)
    atom = OCR.atom_markdown(
        title="Offers — OCR page 4",
        source_id="src-example",
        pdf=Path("offers.pdf"),
        relative_path="Books/offers.pdf",
        page=4,
        text="Offer value",
        mean_confidence=91.0,
        min_confidence=80.0,
        word_count=2,
    )
    (ocr_root / "atoms" / "src-example-page-0004.md").write_text(atom, encoding="utf-8")
    (ocr_root / "reports" / "src-example.json").write_text(
        '{"source_id":"src-example","pages_requested":[4],"pages_completed":1,"low_confidence_pages":[],"results":[{"source_id":"src-example","page":4,"classification":"ocr_text_present"}]}',
        encoding="utf-8",
    )
    ledger = [{"kind": "inventory_record", "record_id": "src-example", "ocr_required_pages": [4], "status": "indexed_text_layer_ocr_pending"}]
    result = BUILDER.integrate_ocr(tmp_path / "pack", ocr_root, ledger)
    assert result["recovered_pages"] == 1
    assert ledger[0]["status"] == "indexed_ocr_recovered_pending_manual_visual_qa"
    assert (tmp_path / "pack" / "ocr" / "src-example-page-0004.md").is_file()


def test_integrate_ocr_marks_inventory_complete_after_manual_review(tmp_path):
    ocr_root = tmp_path / "ocr-results"
    (ocr_root / "atoms").mkdir(parents=True)
    (ocr_root / "reports").mkdir(parents=True)
    (ocr_root / "manual-review").mkdir(parents=True)
    atom = OCR.atom_markdown(
        title="Offers — OCR page 4",
        source_id="src-example",
        pdf=Path("offers.pdf"),
        relative_path="Books/offers.pdf",
        page=4,
        text="Offer value",
        mean_confidence=91.0,
        min_confidence=80.0,
        word_count=2,
    )
    (ocr_root / "atoms" / "src-example-page-0004.md").write_text(atom, encoding="utf-8")
    (ocr_root / "reports" / "src-example.json").write_text(
        '{"source_id":"src-example","pages_requested":[4],"pages_completed":1,"low_confidence_pages":[4],"results":[{"source_id":"src-example","page":4,"classification":"low_confidence_manual_review","manual_review_required":true}]}',
        encoding="utf-8",
    )
    (ocr_root / "manual-review" / "manual-review-manifest.json").write_text(
        '{"visual_qa_status":"manual_review_complete","page_count":1,"reviewed_count":1,"pending_count":0,"decision_counts":{"accept_ocr":1,"graphic_or_blank":0,"reocr_required":0,"unreadable":0}}',
        encoding="utf-8",
    )
    ledger = [{"kind": "inventory_record", "record_id": "src-example", "ocr_required_pages": [4], "status": "indexed_text_layer_ocr_pending"}]
    BUILDER.integrate_ocr(tmp_path / "pack", ocr_root, ledger)
    assert ledger[0]["status"] == "indexed_ocr_recovered_manual_visual_qa_complete"


def test_make_ledger_preserves_evidence_duplicate_canonical():
    manifest = {
        "sources": [
            {"source_id": "src-canonical", "relative_path": "canonical.pdf", "type": "pdf", "hash": "sha256:same", "rights_status": "pending_rights_review", "use_scope": "inventory_only"},
            {"source_id": "src-copy", "relative_path": "copy.pdf", "type": "pdf", "hash": "sha256:same", "rights_status": "pending_rights_review", "use_scope": "inventory_only"},
        ]
    }
    evidence = {
        "sources": [
            {"source_id": "src-canonical", "action": "extracted", "ocr_required_pages": []},
            {"source_id": "src-copy", "action": "deduplicated_by_source_hash", "duplicate_of": "src-canonical"},
        ]
    }
    ledger = BUILDER.make_ledger(manifest, evidence, {"sources": []})
    records = {row["record_id"]: row for row in ledger}
    assert records["src-canonical"]["status"] == "indexed_evidence"
    assert records["src-copy"]["status"] == "duplicate_by_sha256"
    assert records["src-copy"]["duplicate_of"] == "src-canonical"
    assert records["src-canonical"]["title"] == "canonical"
    assert records["src-canonical"]["origin"] == "unmapped_origin"
    assert records["src-canonical"]["extraction_status"] == "indexed"
    assert records["src-copy"]["extraction_status"] == "deduplicated_by_sha256"
    assert records["src-canonical"]["duplicate_group"] == ["src-canonical", "src-copy"]
    assert records["src-copy"]["duplicate_group"] == ["src-canonical", "src-copy"]


def test_inspect_containers_resolves_metadata_only_csv_and_zip(tmp_path):
    csv_path = tmp_path / "library.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Resource Name", "Status"])
        writer.writerow(["Offers", "Organized"])
    zip_path = tmp_path / "library.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("Private & Shared/Offers.md", "already represented")
    manifest = {
        "sources": [
            {"source_id": "src-csv", "type": "csv", "path": str(csv_path), "relative_path": "library.csv", "hash": "sha256:csv", "size_bytes": csv_path.stat().st_size},
            {"source_id": "src-zip", "type": "archive", "path": str(zip_path), "relative_path": "library.zip", "hash": "sha256:zip", "size_bytes": zip_path.stat().st_size},
            {"source_id": "src-member", "type": "markdown", "relative_path": "Offers.md", "size_bytes": len("already represented")},
        ]
    }
    report = BUILDER.inspect_containers(manifest)
    rows = {row["source_id"]: row for row in report["sources"]}
    assert rows["src-csv"]["status"] == "inspected_metadata_manifest_no_unique_knowledge"
    assert rows["src-csv"]["unique_knowledge_ingested"] is False
    assert rows["src-zip"]["status"] == "inspected_container_manifest_no_unique_knowledge"
    assert rows["src-zip"]["manifest_member_matches"] == 1
