"""Regression tests for the ExpertPack doctor command."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


DOCTOR_PATH = Path(__file__).with_name("ep-doctor.py")
SPEC = importlib.util.spec_from_file_location("ep_doctor", DOCTOR_PATH)
assert SPEC and SPEC.loader
ep_doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ep_doctor)


class DoctorValidatorInvocationTests(unittest.TestCase):
    def test_validator_accepts_a_pack_path_with_spaces(self) -> None:
        """The doctor must validate packs in normal Windows workspace paths."""
        with tempfile.TemporaryDirectory(prefix="expertpack path with spaces ") as temp_dir:
            pack = Path(temp_dir) / "test pack"
            pack.mkdir()
            (pack / "manifest.yaml").write_text(
                "\n".join(
                    [
                        'name: "Test Pack"',
                        'slug: "test-pack"',
                        'type: "product"',
                        'version: "1.0.0"',
                        'schema_version: "4.1"',
                        'description: "A minimal validation fixture."',
                        'entry_point: "overview.md"',
                    ]
                ),
                encoding="utf-8",
            )
            (pack / "overview.md").write_text("# Test Pack\n", encoding="utf-8")

            self.assertEqual(ep_doctor._run_validator(str(pack)), 0)


if __name__ == "__main__":
    unittest.main()
