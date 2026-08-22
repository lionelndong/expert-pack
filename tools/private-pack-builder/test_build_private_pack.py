#!/usr/bin/env python3
"""Synthetic end-to-end tests for the rights-gated private pack builder.

No test points at a user folder. Every source, manifest, decision file, PDF,
report, and output pack is created inside TemporaryDirectory.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).parents[2]
SCRIPT = Path(__file__).with_name("build_private_pack.py")
VALIDATOR = REPO_ROOT / "tools" / "validator" / "ep-validate.py"
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
HAS_PDF_TEST_DEPS = bool(importlib.util.find_spec("pypdf")) and bool(
    importlib.util.find_spec("reportlab")
)

MODULE_SPEC = importlib.util.spec_from_file_location("build_private_pack", SCRIPT)
assert MODULE_SPEC and MODULE_SPEC.loader
BUILDER = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = BUILDER
MODULE_SPEC.loader.exec_module(BUILDER)


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def source_record(source_id: str, root: Path, path: Path, source_type: str) -> dict[str, object]:
    return {
        "source_id": source_id,
        "path": str(path),
        "relative_path": path.relative_to(root).as_posix(),
        "type": source_type,
        "rights_status": "pending_rights_review",
        "use_scope": "inventory_only",
        "hash": sha256(path),
        "topic": "synthetic-test",
        "notes": "Synthetic test record.",
        "size_bytes": path.stat().st_size,
        "modified_at": "2026-08-22T00:00:00Z",
    }


def quarantined_record(source_id: str, root: Path, path: Path) -> dict[str, object]:
    return {
        "source_id": source_id,
        "path": str(path),
        "relative_path": path.relative_to(root).as_posix(),
        "type": "text",
        "rights_status": "quarantined_suspect_path",
        "use_scope": "quarantined_no_agent_access",
        "hash": None,
        "topic": "synthetic-test",
        "notes": "Synthetic quarantined record.",
        "quarantine_reason": "Synthetic test quarantine.",
        "size_bytes": path.stat().st_size,
        "modified_at": "2026-08-22T00:00:00Z",
    }


def manifest_for(root: Path, *, sources: list[dict[str, object]], quarantined: list[dict[str, object]] | None = None) -> dict[str, object]:
    quarantined = quarantined or []
    return {
        "manifest_version": "1.0",
        "generated_at": "2026-08-22T00:00:00Z",
        "inventory_mode": "metadata_and_sha256_only",
        "approved_roots": [{"root_id": "root-test", "path": str(root)}],
        "sources": sources,
        "quarantined_sources": quarantined,
        "unreadable_sources": [],
        "scan_warnings": [],
        "summary": {},
    }


def decision(source_id: str, value: str) -> dict[str, str]:
    if value == "approved_internal":
        return {
            "source_id": source_id,
            "decision": value,
            "approved_by": "synthetic-rights-holder",
            "approved_at": "2026-08-22",
            "use_scope": "internal_agent_retrieval",
            "evidence": "Synthetic test authorization.",
        }
    return {"source_id": source_id, "decision": value, "reason": "Synthetic test exclusion."}


def decisions_for(entries: list[dict[str, str]]) -> dict[str, object]:
    return {
        "decision_file_version": "1.0",
        "reviewed_at": "2026-08-22T00:00:00Z",
        "reviewed_by": "synthetic-rights-holder",
        "decisions": entries,
    }


class PrivatePackBuilderTests(unittest.TestCase):
    maxDiff = None

    def run_builder(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def write_inputs(
        self,
        temporary: Path,
        manifest: dict[str, object],
        decisions: dict[str, object],
    ) -> tuple[Path, Path]:
        manifest_path = temporary / "source-manifest.json"
        decisions_path = temporary / "rights-decisions.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
        return manifest_path, decisions_path

    def test_dry_run_creates_no_pack_and_does_not_require_hash_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            root = temporary / "approved"
            root.mkdir()
            source = root / "notes.txt"
            source.write_text("Original text used for inventory.", encoding="utf-8")
            record = source_record("src-dry-run", root, source, "text")
            manifest, decisions = self.write_inputs(
                temporary,
                manifest_for(root, sources=[record]),
                decisions_for([decision("src-dry-run", "approved_internal")]),
            )
            # A changed file would fail the actual hash gate. Dry run must still
            # be metadata-only and must not open the source.
            source.write_text("Changed after inventory.", encoding="utf-8")
            report = temporary / "dry-run.json"

            completed = self.run_builder(
                ["--manifest", str(manifest), "--rights-decisions", str(decisions), "--report", str(report)]
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(result["mode"], "dry_run_metadata_only")
            self.assertEqual(result["sources"][0]["action"], "would_extract")
            self.assertFalse((temporary / "private-source-pack").exists())

    def test_source_hash_binds_to_the_exact_bytes_extracted(self) -> None:
        """The builder must not hash through one handle and extract through another."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            root = temporary / "approved"
            root.mkdir()
            source = root / "notes.txt"
            expected_bytes = b"Synthetic source bytes that must be hash-verified."
            source.write_bytes(expected_bytes)
            record = source_record("src-single-read", root, source, "text")

            # `Path.read_bytes()` opens one extraction stream. A separate
            # hash-read would recreate the old hash-then-read race and result
            # in two opens of the source path.
            open_count = 0
            original_open = Path.open

            def count_open(path: Path, *args: object, **kwargs: object):
                nonlocal open_count
                open_count += 1
                return original_open(path, *args, **kwargs)

            with mock.patch.object(Path, "open", new=count_open):
                source_path, raw = BUILDER.read_and_verify_source(record, [root.resolve()])

            self.assertEqual(source_path, source.resolve())
            self.assertEqual(raw, expected_bytes)
            self.assertEqual(open_count, 1)

    def test_content_build_fails_closed_without_pdf_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            root = temporary / "approved"
            root.mkdir()
            source = root / "approved.pdf"
            source.write_bytes(b"Synthetic PDF bytes; the dependency check runs before parsing.")
            manifest, decisions = self.write_inputs(
                temporary,
                manifest_for(root, sources=[source_record("src-pdf", root, source, "pdf")]),
                decisions_for([decision("src-pdf", "approved_internal")]),
            )
            output_parent = temporary / "output"
            output_parent.mkdir()
            output = output_parent / "must-not-exist"
            report = temporary / "dependency-report.json"
            arguments = [
                str(SCRIPT),
                "--manifest",
                str(manifest),
                "--rights-decisions",
                str(decisions),
                "--allow-content-extraction",
                "--output",
                str(output),
                "--report",
                str(report),
            ]

            with (
                mock.patch.object(sys, "argv", arguments),
                mock.patch.dict(sys.modules, {"pypdf": None}),
                mock.patch("sys.stdout", new_callable=io.StringIO),
            ):
                exit_code = BUILDER.main()

            self.assertEqual(exit_code, 2)
            self.assertFalse(output.exists())
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["configuration_errors"][0]["code"], "pdf_dependency_missing")

    def test_text_conversion_deduplicates_and_strictly_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            root = temporary / "approved"
            root.mkdir()
            source = root / "playbook.md"
            source_text = "\n".join(f"Line {number}: synthetic source content for provenance testing." for number in range(1, 14))
            source.write_text(source_text + "\n", encoding="utf-8")
            duplicate = root / "duplicate.txt"
            duplicate.write_bytes(source.read_bytes())
            records = [
                source_record("src-playbook", root, source, "markdown"),
                source_record("src-duplicate", root, duplicate, "text"),
            ]
            manifest, decisions = self.write_inputs(
                temporary,
                manifest_for(root, sources=records),
                decisions_for(
                    [
                        decision("src-playbook", "approved_internal"),
                        decision("src-duplicate", "approved_internal"),
                    ]
                ),
            )
            output_parent = temporary / "output"
            output_parent.mkdir()
            output = output_parent / "synthetic-private-pack"
            report = temporary / "build-report.json"

            completed = self.run_builder(
                [
                    "--manifest",
                    str(manifest),
                    "--rights-decisions",
                    str(decisions),
                    "--allow-content-extraction",
                    "--output",
                    str(output),
                    "--pack-name",
                    "Synthetic private pack",
                    "--pack-slug",
                    "synthetic-private-pack",
                    "--max-source-chars",
                    "256",
                    "--report",
                    str(report),
                ]
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            build_report = json.loads(report.read_text(encoding="utf-8"))
            by_id = {entry["source_id"]: entry for entry in build_report["sources"]}
            self.assertEqual(by_id["src-playbook"]["action"], "extracted")
            self.assertGreater(by_id["src-playbook"]["atoms_created"], 1)
            self.assertEqual(by_id["src-duplicate"]["action"], "deduplicated_by_source_hash")
            self.assertFalse((output / "sources").exists())

            atoms = sorted((output / "concepts").glob("src-*.md"))
            self.assertEqual(len(atoms), by_id["src-playbook"]["atoms_created"])
            atom_text = atoms[0].read_text(encoding="utf-8")
            self.assertIn('source_id: "src-playbook"', atom_text)
            self.assertIn('source_hash: "sha256:', atom_text)
            self.assertIn("start_line:", atom_text)
            self.assertIn("end_line:", atom_text)
            self.assertIn("related:", atom_text)

            validator_python = VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable)
            validation = subprocess.run(
                [str(validator_python), str(VALIDATOR), str(output), "--strict"],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
            self.assertIn("0 errors, 0 warnings", validation.stdout + validation.stderr)

    @unittest.skipUnless(HAS_PDF_TEST_DEPS, "pypdf and reportlab are required for synthetic PDF tests")
    def test_text_layer_pdf_extracts_and_scan_only_pdf_is_reported(self) -> None:
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            root = temporary / "approved"
            root.mkdir()
            text_pdf = root / "text-layer.pdf"
            writer = canvas.Canvas(str(text_pdf))
            writer.drawString(72, 720, "Synthetic PDF text on page one.")
            writer.showPage()
            writer.drawString(72, 720, "Synthetic PDF text on page two.")
            writer.save()
            scan_pdf = root / "scan-only.pdf"
            scan_writer = canvas.Canvas(str(scan_pdf))
            scan_writer.rect(72, 680, 200, 80, fill=1)
            scan_writer.save()
            records = [
                source_record("src-text-pdf", root, text_pdf, "pdf"),
                source_record("src-scan-pdf", root, scan_pdf, "pdf"),
            ]
            manifest, decisions = self.write_inputs(
                temporary,
                manifest_for(root, sources=records),
                decisions_for(
                    [
                        decision("src-text-pdf", "approved_internal"),
                        decision("src-scan-pdf", "approved_internal"),
                    ]
                ),
            )
            output_parent = temporary / "output"
            output_parent.mkdir()
            output = output_parent / "synthetic-pdf-pack"
            report = temporary / "pdf-report.json"

            completed = self.run_builder(
                [
                    "--manifest",
                    str(manifest),
                    "--rights-decisions",
                    str(decisions),
                    "--allow-content-extraction",
                    "--output",
                    str(output),
                    "--pack-slug",
                    "synthetic-pdf-pack",
                    "--report",
                    str(report),
                ]
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            by_id = {entry["source_id"]: entry for entry in json.loads(report.read_text())["sources"]}
            self.assertEqual(by_id["src-text-pdf"]["action"], "extracted")
            self.assertEqual(by_id["src-scan-pdf"]["action"], "ocr_required")
            self.assertEqual(by_id["src-scan-pdf"]["ocr_required_pages"], [1])
            atom_text = next((output / "concepts").glob("src-src-text-pdf-*.md")).read_text(encoding="utf-8")
            self.assertIn("start_page: 1", atom_text)
            self.assertIn("end_page: 1", atom_text)

    def test_quarantined_approval_blocks_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            root = temporary / "approved"
            root.mkdir()
            safe = root / "safe.txt"
            safe.write_text("safe synthetic content", encoding="utf-8")
            suspect = root / "suspect.txt"
            suspect.write_text("quarantined synthetic content", encoding="utf-8")
            manifest, decisions = self.write_inputs(
                temporary,
                manifest_for(
                    root,
                    sources=[source_record("src-safe", root, safe, "text")],
                    quarantined=[quarantined_record("qsrc-suspect", root, suspect)],
                ),
                decisions_for(
                    [
                        decision("src-safe", "approved_internal"),
                        decision("qsrc-suspect", "approved_internal"),
                    ]
                ),
            )
            output_parent = temporary / "output"
            output_parent.mkdir()
            output = output_parent / "must-not-exist"
            report = temporary / "quarantine-report.json"

            completed = self.run_builder(
                [
                    "--manifest",
                    str(manifest),
                    "--rights-decisions",
                    str(decisions),
                    "--allow-content-extraction",
                    "--output",
                    str(output),
                    "--report",
                    str(report),
                ]
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(output.exists())
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["configuration_errors"][0]["code"], "attempt_to_approve_quarantined_source")


if __name__ == "__main__":
    unittest.main()
