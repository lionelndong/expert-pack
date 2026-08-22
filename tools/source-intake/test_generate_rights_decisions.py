#!/usr/bin/env python3
"""Synthetic tests for profile-specific rights-decision generation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from jsonschema import validate as validate_json_schema
except ImportError:  # Optional project dependency.
    validate_json_schema = None


SCRIPT = Path(__file__).with_name("generate_rights_decisions.py")
SCHEMA = Path(__file__).parents[2] / "config" / "source-intake" / "rights-decisions.schema.json"

MODULE_SPEC = importlib.util.spec_from_file_location("generate_rights_decisions", SCRIPT)
assert MODULE_SPEC and MODULE_SPEC.loader
GENERATOR = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(GENERATOR)


PAPERCLIP_ROOT = r"C:\sources\paperclip.ai\alex-hormozi-skills"
RAW_ROOT = r"C:\sources\ExportBlock\Private & Shared\Alex Hormozi Knowledge Library"
BOOK_ROOT = r"C:\sources\book-to-skill-master\ALEX HORMOZI"


def record(source_id: str, path: str, relative_path: str, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "source_id": source_id,
        "path": path,
        "relative_path": relative_path,
        "type": "markdown" if relative_path.lower().endswith((".md", ".markdown")) else "pdf",
        "rights_status": "pending_rights_review",
        "use_scope": "inventory_only",
        "hash": "sha256:" + "a" * 64,
    }
    result.update(overrides)
    return result


def synthetic_manifest() -> dict[str, object]:
    return {
        "manifest_version": "1.0",
        "approved_roots": [
            {"path": RAW_ROOT},
            {"path": BOOK_ROOT},
            {"path": PAPERCLIP_ROOT},
        ],
        "sources": [
            record("raw-pdf", RAW_ROOT + r"\Complete Playbooks\Offers.pdf", "Complete Playbooks/Offers.pdf"),
            record("raw-readme", RAW_ROOT + r"\README.md", "README.md"),
            record(
                "raw-export-index",
                RAW_ROOT + r"\Additional Resources 291c65b720ff806fb1c2c10acf2b1e83.md",
                "Additional Resources 291c65b720ff806fb1c2c10acf2b1e83.md",
            ),
            record("raw-book-markdown", RAW_ROOT + r"\Books\A Book.md", "Books/A Book.md"),
            record("book-pdf", BOOK_ROOT + r"\Roadmap.pdf", "Roadmap.pdf"),
            record("skill-md", PAPERCLIP_ROOT + r"\hormozi-100m-offers\SKILL.md", "hormozi-100m-offers/SKILL.md"),
            record("skill-source", PAPERCLIP_ROOT + r"\hormozi-100m-offers\source\chapter.md", "hormozi-100m-offers/source/chapter.md"),
            record("skill-pdf", PAPERCLIP_ROOT + r"\hormozi-100m-offers\reference.pdf", "hormozi-100m-offers/reference.pdf"),
            record(
                "normal-blocked",
                RAW_ROOT + r"\Blocked.pdf",
                "Blocked.pdf",
                rights_status="quarantined_manual_review",
                use_scope="quarantined_no_agent_access",
            ),
        ],
        "quarantined_sources": [
            record(
                "qsrc",
                RAW_ROOT + r"\LEAKED.pdf",
                "LEAKED.pdf",
                rights_status="quarantined_suspect_path",
                use_scope="quarantined_no_agent_access",
                hash=None,
            )
        ],
        "unreadable_sources": [
            record(
                "unreadable",
                RAW_ROOT + r"\Unreadable.pdf",
                "Unreadable.pdf",
                rights_status="pending_inventory_read_error",
                use_scope="not_eligible_read_error",
                hash=None,
            )
        ],
    }


class GenerateRightsDecisionsTests(unittest.TestCase):
    def generate(self, profile: str) -> dict[str, object]:
        return GENERATOR.generate_decisions(
            synthetic_manifest(),
            profile=profile,
            approved_by="authorized reviewer",
            approved_at="2026-08-22",
            evidence="Company authorization for internal agent retrieval.",
            reviewed_by="authorized reviewer",
            reviewed_at="2026-08-22T12:00:00Z",
        )

    @staticmethod
    def by_id(document: dict[str, object]) -> dict[str, dict[str, str]]:
        return {entry["source_id"]: entry for entry in document["decisions"]}  # type: ignore[index]

    def assert_approval(self, decision: dict[str, str]) -> None:
        self.assertEqual(decision["decision"], "approved_internal")
        self.assertEqual(decision["approved_by"], "authorized reviewer")
        self.assertEqual(decision["approved_at"], "2026-08-22")
        self.assertEqual(decision["evidence"], "Company authorization for internal agent retrieval.")
        self.assertEqual(decision["use_scope"], "internal_agent_retrieval")

    def test_evidence_profile_selects_raw_and_book_evidence_not_skill_artifacts(self) -> None:
        decisions = self.by_id(self.generate("evidence"))
        self.assert_approval(decisions["raw-pdf"])
        self.assert_approval(decisions["raw-book-markdown"])
        self.assert_approval(decisions["book-pdf"])
        self.assertEqual(decisions["raw-readme"]["decision"], "excluded")
        self.assertIn("navigation metadata", decisions["raw-readme"]["reason"])
        self.assertEqual(decisions["raw-export-index"]["decision"], "excluded")
        self.assertIn("navigation metadata", decisions["raw-export-index"]["reason"])
        for source_id in ("skill-md", "skill-source", "skill-pdf"):
            self.assertEqual(decisions[source_id]["decision"], "excluded")
            self.assertIn("skills pack", decisions[source_id]["reason"])

    def test_skills_profile_selects_only_curated_paperclip_markdown(self) -> None:
        decisions = self.by_id(self.generate("skills"))
        self.assert_approval(decisions["skill-md"])
        self.assertEqual(decisions["skill-source"]["decision"], "excluded")
        self.assertIn("source/ material", decisions["skill-source"]["reason"])
        self.assertEqual(decisions["skill-pdf"]["decision"], "excluded")
        self.assertIn("Markdown", decisions["skill-pdf"]["reason"])
        for source_id in ("raw-pdf", "raw-readme", "raw-book-markdown", "book-pdf"):
            self.assertEqual(decisions[source_id]["decision"], "excluded")
            self.assertIn("evidence pack", decisions[source_id]["reason"])

    def test_evidence_profile_excludes_records_outside_the_reviewed_library_roots(self) -> None:
        manifest = synthetic_manifest()
        external_root = r"C:\sources\unrelated-material"
        manifest["approved_roots"].append({"path": external_root})  # type: ignore[index]
        manifest["sources"].append(  # type: ignore[index]
            record("unrelated", external_root + r"\Other.pdf", "Other.pdf")
        )

        document = GENERATOR.generate_decisions(
            manifest,
            profile="evidence",
            approved_by="authorized reviewer",
            approved_at="2026-08-22",
            evidence="Company authorization for internal agent retrieval.",
            reviewed_by="authorized reviewer",
            reviewed_at="2026-08-22T12:00:00Z",
        )

        decision = self.by_id(document)["unrelated"]
        self.assertEqual(decision["decision"], "excluded")
        self.assertIn("outside the reviewed", decision["reason"])

    def test_quarantined_and_unreadable_records_are_never_approved(self) -> None:
        for profile in ("evidence", "skills"):
            decisions = self.by_id(self.generate(profile))
            self.assertEqual(decisions["normal-blocked"]["decision"], "quarantined_inventory_record")
            self.assertEqual(decisions["qsrc"]["decision"], "quarantined_inventory_record")
            self.assertEqual(decisions["unreadable"]["decision"], "quarantined_unreadable_inventory_record")
            self.assertNotEqual(decisions["normal-blocked"]["decision"], "approved_internal")
            self.assertNotEqual(decisions["qsrc"]["decision"], "approved_internal")
            self.assertNotEqual(decisions["unreadable"]["decision"], "approved_internal")

    def test_output_is_complete_and_conforms_to_schema_when_available(self) -> None:
        document = self.generate("skills")
        GENERATOR.validate_generated_document(document)
        self.assertEqual(len(document["decisions"]), 11)
        self.assertEqual(len({entry["source_id"] for entry in document["decisions"]}), 11)
        if validate_json_schema is not None:
            validate_json_schema(document, json.loads(SCHEMA.read_text(encoding="utf-8")))

    def test_cli_refuses_to_overwrite_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            manifest_path = temporary / "manifest.json"
            output = temporary / "decisions.json"
            manifest_path.write_text(json.dumps(synthetic_manifest()), encoding="utf-8")
            output.write_text("already here", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--manifest",
                    str(manifest_path),
                    "--profile",
                    "evidence",
                    "--approved-by",
                    "authorized reviewer",
                    "--approved-at",
                    "2026-08-22",
                    "--evidence",
                    "authorization",
                    "--out",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("refusing to overwrite", completed.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "already here")


if __name__ == "__main__":
    unittest.main()
