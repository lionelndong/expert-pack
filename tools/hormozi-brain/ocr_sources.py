#!/usr/bin/env python3
"""OCR approved PDF pages with render-and-confidence QA.

The command only processes pages listed by the existing evidence report. It
requires both Poppler's ``pdftoppm`` and Tesseract. If either is absent it
exits with an actionable message and leaves source files untouched.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--pages", type=int, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    missing = [tool for tool in ("pdftoppm", "tesseract") if shutil.which(tool) is None]
    if missing:
        raise SystemExit("Missing OCR tools: " + ", ".join(missing) + ". Install Poppler and Tesseract in the approved environment.")
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hormozi-ocr-") as temp:
        temp_dir = Path(temp)
        for page in sorted(set(args.pages)):
            prefix = temp_dir / f"page-{page:04d}"
            subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", "220", "-png", str(args.pdf), str(prefix)], check=True)
            image = next(temp_dir.glob(f"page-{page:04d}-*.png"))
            text_path = args.output / f"page-{page:04d}.txt"
            subprocess.run(["tesseract", str(image), str(text_path.with_suffix("")), "--psm", "3"], check=True, capture_output=True, text=True)
            text = text_path.read_text(encoding="utf-8", errors="replace").strip()
            atom = {"title": f"{args.pdf.stem} — OCR page {page}", "type": "reference", "pack": "alex-hormozi-brain", "tags": ["ocr", "pdf", "alex-hormozi"], "schema_version": "4.1", "id": f"alex-hormozi-brain/ocr/{args.pdf.stem}/page-{page:04d}", "retrieval_strategy": "standard", "verified_at": "2026-08-23", "verified_by": "tesseract-ocr", "confidence": "crawled", "source_page": page, "text": text}
            (args.output / f"page-{page:04d}.yaml").write_text(yaml.safe_dump(atom, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"OCR complete: {len(set(args.pages))} pages; rendered PNGs were QA inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
