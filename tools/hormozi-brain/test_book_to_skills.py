from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("build_brain.py")
SPEC = importlib.util.spec_from_file_location("hormozi_build_brain_book_to_skills", MODULE_PATH)
assert SPEC and SPEC.loader
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def test_book_to_skills_aliases_are_hash_deduplicated(tmp_path):
    root = tmp_path / "skills" / "alex-hormozi"
    (root / "package-a").mkdir(parents=True)
    (root / "package-a" / "SKILL.md").write_text("canonical skill", encoding="utf-8")
    (root / "package-a" / "patterns.md").write_text("unmatched", encoding="utf-8")
    canonical = tmp_path / "paperclip" / "package-a" / "SKILL.md"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("canonical skill", encoding="utf-8")
    ledger = [{
        "kind": "inventory_record",
        "record_id": "src-paperclip-skill",
        "sha256": BUILD.sha256_file(canonical),
    }]

    report = BUILD.audit_book_to_skills(root, ledger)

    assert report["status"] == "incomplete_external_export"
    assert report["source_file_count"] == 2
    assert report["matched_hash_count"] == 1
    assert report["unmatched_count"] == 1
    match = next(item for item in report["aliases"] if item["status"] == "duplicate_by_sha256")
    assert match["duplicate_of"] == ["src-paperclip-skill"]


def test_book_to_skills_missing_root_is_explicit(tmp_path):
    report = BUILD.audit_book_to_skills(tmp_path / "missing", [])
    assert report["status"] == "source_not_found"
    assert report["source_file_count"] == 0
