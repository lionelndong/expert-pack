import importlib.util
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
