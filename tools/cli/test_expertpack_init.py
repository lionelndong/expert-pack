"""Regression tests for ExpertPack scaffolding."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "tools" / "cli" / "expertpack.py"


class PersonPackScaffoldTests(unittest.TestCase):
    def test_person_pack_scaffold_passes_strict_validation(self) -> None:
        """A fresh person pack must meet its own filename-prefix contract."""
        with tempfile.TemporaryDirectory(prefix="expertpack scaffold with spaces ") as temp_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "init",
                    "test-person",
                    "--type",
                    "person",
                    "--output",
                    temp_dir,
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
