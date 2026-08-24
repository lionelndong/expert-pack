#!/usr/bin/env python3
"""OCR approved PDF pages with rendered visual-QA artifacts.

The command only processes pages listed by the existing evidence report. It
requires Poppler's ``pdftoppm`` and Tesseract. Originals are read-only; each
page becomes a provenance-rich Markdown atom and a retained PNG for visual QA.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml


def slug(value: str) -> str:
    result = "".join(char.lower() if char.isalnum() else "-" for char in value)
    return "-".join(part for part in result.split("-") if part) or "source"


def parse_tsv(path: Path) -> tuple[str, float | None, float | None, int]:
    lines: list[str] = []
    current_line: tuple[str, str, str] | None = None
    words: list[tuple[tuple[str, str, str], str]] = []
    confidences: list[float] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            text = (row.get("text") or "").strip()
            if not text:
                continue
            key = (row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))
            words.append((key, text))
            try:
                confidence = float(row.get("conf", "-1"))
            except ValueError:
                confidence = -1.0
            if confidence >= 0:
                confidences.append(confidence)
    for key, text in words:
        if current_line is not None and key != current_line:
            lines.append(" ".join(item for item_key, item in words if item_key == current_line))
        current_line = key
    if current_line is not None:
        lines.append(" ".join(item for item_key, item in words if item_key == current_line))
    text = "\n".join(line for line in lines if line).strip()
    return (
        text,
        sum(confidences) / len(confidences) if confidences else None,
        min(confidences) if confidences else None,
        len(words),
    )


def atom_markdown(*, title: str, source_id: str, pdf: Path, relative_path: str, page: int, text: str, mean_confidence: float | None, min_confidence: float | None, word_count: int) -> str:
    body = (
        f"# {title}\n\n"
        "> OCR-derived source material for internal, source-grounded decision support; "
        "not a current statement, endorsement, or impersonation of Alex Hormozi.\n\n"
        "## Provenance\n\n"
        f"- Source ID: `{source_id}`\n"
        f"- Source PDF: `{pdf}`\n"
        f"- Relative source path: `{relative_path}`\n"
        f"- Page: {page}\n"
        "- Extraction method: Poppler render + Tesseract OCR\n"
        f"- OCR mean confidence: {mean_confidence}\n"
        f"- OCR minimum confidence: {min_confidence}\n"
        f"- OCR word count: {word_count}\n\n"
        "## OCR text\n\n```text\n"
        + text
        + "\n```\n"
    )
    frontmatter = {
        "title": title,
        "type": "reference",
        "pack": "alex-hormozi-brain",
        "tags": ["ocr", "pdf", "alex-hormozi", "page-level-provenance"],
        "schema_version": "4.1",
        "id": f"alex-hormozi-brain/ocr/{source_id}/page-{page:04d}",
        "content_hash": f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}",
        "verified_at": date.today().isoformat(),
        "verified_by": "tesseract-ocr",
        "confidence": "crawled",
        "retrieval_strategy": "standard",
        "source_id": source_id,
        "source_page": page,
        "ocr_mean_confidence": mean_confidence,
        "ocr_min_confidence": min_confidence,
        "ocr_word_count": word_count,
    }
    return "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip() + "\n---\n" + body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--pages", type=int, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--relative-path", default="")
    parser.add_argument("--qa-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--low-confidence-threshold", type=float, default=60.0)
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()
    missing = [tool for tool in ("pdftoppm", "tesseract") if shutil.which(tool) is None]
    if missing:
        raise SystemExit("Missing OCR tools: " + ", ".join(missing) + ". Install Poppler and Tesseract in the approved environment.")
    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")
    pages = sorted({page for page in args.pages if page > 0})
    if not pages:
        raise SystemExit("At least one positive page number is required")
    args.output.mkdir(parents=True, exist_ok=True)
    qa_dir = (args.qa_dir or args.output.parent / "qa" / f"{slug(args.source_id)}-{slug(args.pdf.stem)}").resolve()
    qa_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="hormozi-ocr-") as temp:
        temp_dir = Path(temp)
        repaired_pdf: Path | None = None
        for page in pages:
            prefix = temp_dir / f"page-{page:04d}"
            render_pdf = repaired_pdf or args.pdf
            try:
                subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(args.dpi), "-png", str(render_pdf), str(prefix)], check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError:
                if repaired_pdf is not None:
                    raise
                try:
                    import pikepdf
                    repaired_pdf = temp_dir / "repaired-source.pdf"
                    with pikepdf.Pdf.open(args.pdf, attempt_recovery=True) as repaired:
                        repaired.save(repaired_pdf)
                except Exception as error:
                    raise RuntimeError(f"Poppler could not render {args.pdf} and recovery failed: {type(error).__name__}") from error
                subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(args.dpi), "-png", str(repaired_pdf), str(prefix)], check=True, capture_output=True, text=True)
            images = sorted(temp_dir.glob(f"page-{page:04d}-*.png"))
            if not images:
                raise RuntimeError(f"Poppler did not render page {page} of {args.pdf}")
            image = images[0]
            qa_image = qa_dir / f"page-{page:04d}.png"
            shutil.copy2(image, qa_image)
            tsv_base = temp_dir / f"ocr-{page:04d}"
            subprocess.run(["tesseract", str(image), str(tsv_base), "--psm", "3", "tsv"], check=True, capture_output=True, text=True)
            text, mean_confidence, min_confidence, word_count = parse_tsv(tsv_base.with_suffix(".tsv"))
            needs_review = mean_confidence is None or mean_confidence < args.low_confidence_threshold or (min_confidence is not None and min_confidence < 25.0)
            title = f"{args.pdf.stem} — OCR page {page}"
            destination = args.output / f"{slug(args.source_id)}-page-{page:04d}.md"
            destination.write_text(atom_markdown(title=title, source_id=args.source_id, pdf=args.pdf, relative_path=args.relative_path, page=page, text=text, mean_confidence=mean_confidence, min_confidence=min_confidence, word_count=word_count), encoding="utf-8", newline="\n")
            results.append({"source_id": args.source_id, "pdf": str(args.pdf), "relative_path": args.relative_path, "page": page, "output": str(destination), "qa_image": str(qa_image), "mean_confidence": mean_confidence, "min_confidence": min_confidence, "word_count": word_count, "needs_manual_review": needs_review})
    report = {
        "report_version": "1.0",
        "source_id": args.source_id,
        "pdf": str(args.pdf),
        "relative_path": args.relative_path,
        "pages_requested": pages,
        "pages_completed": len(results),
        "low_confidence_pages": [item["page"] for item in results if item["needs_manual_review"]],
        "visual_qa_dir": str(qa_dir),
        "visual_qa_status": "rendered_pending_manual_review",
        "results": results,
    }
    report_path = args.report or args.output / f"{slug(args.source_id)}-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"OCR complete: {len(results)} pages; rendered PNGs are retained at {qa_dir}; manual review status is pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
