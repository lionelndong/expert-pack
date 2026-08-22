#!/usr/bin/env python3
"""Focused regression tests for the ExpertPack validator."""

from __future__ import annotations

from collections import defaultdict
import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("ep-validate.py")
SPEC = importlib.util.spec_from_file_location("ep_validate", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CountingDict(dict):
    """Records full-map scans so related-link lookup stays linear."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items_calls = 0

    def items(self):  # type: ignore[override]
        self.items_calls += 1
        return super().items()


class BidirectionalRelatedTests(unittest.TestCase):
    def test_uses_basename_lookup_not_a_full_map_scan_per_related_link(self) -> None:
        validator = MODULE.Validator(".")
        count = 200
        frontmatter = CountingDict()
        basenames = defaultdict(list)

        for index in range(count):
            filename = f"atom-{index:03d}.md"
            previous = f"atom-{(index - 1) % count:03d}.md"
            next_item = f"atom-{(index + 1) % count:03d}.md"
            path = f"concepts/{filename}"
            frontmatter[path] = {"related": [previous, next_item]}
            basenames[filename].append(path)

        validator.fm = frontmatter
        validator.basenames = basenames
        validator.all_basenames = set(basenames)
        validator.check_bidirectional_related()

        self.assertEqual(validator.issues, [])
        self.assertLessEqual(frontmatter.items_calls, 2)


if __name__ == "__main__":
    unittest.main()
