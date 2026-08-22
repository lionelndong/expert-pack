#!/usr/bin/env python3
"""Smoke tests for the metadata-only source intake inventory."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from jsonschema import validate as validate_json_schema
except ImportError:  # The project declares this as an optional eval dependency.
    validate_json_schema = None


SCRIPT = Path(__file__).with_name("inventory_sources.py")
SCHEMA = Path(__file__).parents[2] / "config" / "source-intake" / "source-manifest.schema.json"


class InventorySourcesTests(unittest.TestCase):
    def test_inventory_hashes_safe_files_and_quarantines_suspect_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            root = temporary / "approved"
            nested = root / "Offers"
            nested.mkdir(parents=True)
            (nested / "offer-playbook.pdf").write_bytes(b"safe source bytes")
            (root / "LEAKED_Pricing_Playbook.pdf").write_bytes(b"must not be hashed")
            output = temporary / "source-manifest.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--approved-root",
                    str(root),
                    "--out",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"]["files_scanned"], 2)
            self.assertEqual(manifest["summary"]["inventory_only_sources"], 1)
            self.assertEqual(manifest["summary"]["quarantined_sources"], 1)

            source = manifest["sources"][0]
            self.assertEqual(source["relative_path"], "Offers/offer-playbook.pdf")
            self.assertEqual(source["rights_status"], "pending_rights_review")
            self.assertEqual(source["use_scope"], "inventory_only")
            self.assertTrue(source["hash"].startswith("sha256:"))
            self.assertEqual(source["topic"], "offers")

            quarantined = manifest["quarantined_sources"][0]
            self.assertEqual(quarantined["hash"], None)
            self.assertEqual(quarantined["use_scope"], "quarantined_no_agent_access")
            self.assertIn("leaked", quarantined["quarantine_reason"])

    def test_refuses_to_write_inside_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            root = temporary / "approved"
            root.mkdir()
            (root / "safe.txt").write_text("safe", encoding="utf-8")
            output = root / "manifest.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--approved-root",
                    str(root),
                    "--out",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("outside every approved root", completed.stderr)
            self.assertFalse(output.exists())

    @unittest.skipIf(validate_json_schema is None, "jsonschema optional dependency is not installed")
    def test_generated_manifest_conforms_to_its_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            root = temporary / "approved"
            root.mkdir()
            (root / "safe.txt").write_text("safe", encoding="utf-8")
            output = temporary / "source-manifest.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--approved-root",
                    str(root),
                    "--out",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
            manifest = json.loads(output.read_text(encoding="utf-8"))
            validate_json_schema(manifest, schema)


if __name__ == "__main__":
    unittest.main()
