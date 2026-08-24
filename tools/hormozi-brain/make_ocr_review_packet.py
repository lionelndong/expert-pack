#!/usr/bin/env python3
"""Create a visual-review packet for every low-confidence OCR page.

The packet is deliberately pending-only: it never guesses that OCR is correct.
Reviewers can inspect the generated source-grouped sheets and use the manifest
to record explicit decisions against stable source/page identifiers.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw


def _page_number(item: dict) -> int:
    return int(item.get("page", 0))


def _make_sheet(cards: list[dict], output: Path, *, columns: int = 4, per_sheet: int = 24) -> list[str]:
    output.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []
    for batch_index in range(0, len(cards), per_sheet):
        batch = cards[batch_index:batch_index + per_sheet]
        card_width, card_height, label_height = 420, 560, 120
        rows = (len(batch) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * card_width, rows * card_height), "white")
        draw = ImageDraw.Draw(sheet)
        for index, card in enumerate(batch):
            image_path = Path(str(card["qa_image"]))
            if not image_path.is_file():
                raise FileNotFoundError(f"QA image missing for {card['review_id']}: {image_path}")
            with Image.open(image_path).convert("RGB") as image:
                image.thumbnail(
                    (card_width - 20, card_height - label_height - 10),
                    Image.Resampling.BILINEAR,
                )
                x = (index % columns) * card_width + (card_width - image.width) // 2
                y = (index // columns) * card_height + 5
                sheet.paste(image, (x, y))
            tx = (index % columns) * card_width + 8
            ty = (index // columns) * card_height + card_height - label_height
            label = (
                f"{card['source_id']} page {card['page']}\n"
                f"{card['classification']}\n"
                f"mean={card.get('mean_confidence')} min={card.get('min_confidence')}"
            )
            draw.multiline_text((tx, ty), label, fill="black", spacing=2)
        sheet_path = output / f"{cards[0]['source_id']}-{batch_index // per_sheet + 1:03d}.jpg"
        sheet.save(sheet_path, format="JPEG", quality=85, optimize=True)
        generated.append(str(sheet_path))
    return generated


def build_packet(root: Path, output: Path, *, reuse_complete: bool = False) -> dict:
    previous_manifest: dict = {}
    previous_path = output / "manual-review-manifest.json"
    if previous_path.is_file():
        try:
            loaded = json.loads(previous_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous_manifest = loaded
        except (OSError, json.JSONDecodeError):
            # A malformed prior packet must not prevent a fresh, explicit packet.
            previous_manifest = {}
    previous_pages = {
        str(page.get("review_id")): page
        for page in previous_manifest.get("pages", [])
        if isinstance(page, dict) and page.get("review_id")
    }
    pages: list[dict] = []
    by_source: dict[str, list[dict]] = {}
    for report_path in sorted((root / "reports").glob("*.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        source_id = str(report.get("source_id"))
        for item in report.get("results", []):
            if not item.get("manual_review_required"):
                continue
            page = _page_number(item)
            record = {
                "review_id": f"{source_id}:page-{page:04d}",
                "source_id": source_id,
                "relative_source_path": report.get("relative_path"),
                "page": page,
                "classification": item.get("classification"),
                "mean_confidence": item.get("mean_confidence"),
                "min_confidence": item.get("min_confidence"),
                "word_count": item.get("word_count"),
                "qa_image": str(item.get("qa_image")),
                "ocr_atom": str(root / "atoms" / f"{source_id}-page-{page:04d}.md"),
                "review_status": "pending",
                "review_decision": None,
                "review_notes": None,
            }
            prior = previous_pages.get(record["review_id"])
            if prior and prior.get("review_status") == "reviewed" and prior.get("review_decision"):
                record["review_status"] = "reviewed"
                record["review_decision"] = prior["review_decision"]
                record["review_notes"] = prior.get("review_notes")
                record["reviewed_at_utc"] = prior.get("reviewed_at_utc")
            pages.append(record)
            by_source.setdefault(source_id, []).append(record)
    current_review_ids = {str(page["review_id"]) for page in pages}
    previous_review_ids = set(previous_pages)
    can_reuse_complete = (
        reuse_complete
        and previous_manifest.get("visual_qa_status") == "manual_review_complete"
        and current_review_ids == previous_review_ids
        and pages
        and all(page.get("review_status") == "reviewed" for page in pages)
    )
    if can_reuse_complete:
        # Individual QA renders may be cleaned up after review to save disk,
        # while the source-grouped sheets and signed decisions remain the
        # durable visual-QA evidence. Never reuse a complete packet if the
        # underlying page set changed.
        sheets = [str(path) for path in previous_manifest.get("sheets", [])]
    else:
        sheets = []
        for source_id, records in sorted(by_source.items()):
            sheets.extend(_make_sheet(records, output / "sheets"))
    manifest = {
        "report_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "visual_qa_status": "pending_manual_review",
        "review_options": ["accept_ocr", "reocr_required", "graphic_or_blank", "unreadable"],
        "source_count": len(by_source),
        "page_count": len(pages),
        "sheets": sheets,
        "pages": pages,
    }
    if pages and all(page.get("review_status") == "reviewed" for page in pages):
        manifest["visual_qa_status"] = "manual_review_complete"
    manifest["reviewed_count"] = sum(page.get("review_status") == "reviewed" for page in pages)
    manifest["pending_count"] = len(pages) - manifest["reviewed_count"]
    manifest["decision_counts"] = {
        decision: sum(page.get("review_decision") == decision for page in pages)
        for decision in ("accept_ocr", "reocr_required", "graphic_or_blank", "unreadable")
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "manual-review-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("private-input/ocr-results"))
    parser.add_argument("--output", type=Path, default=Path("private-input/ocr-results/manual-review"))
    parser.add_argument(
        "--reuse-complete",
        action="store_true",
        help="reuse a previously complete packet when the reviewed page set is unchanged",
    )
    args = parser.parse_args()
    manifest = build_packet(args.root.resolve(), args.output.resolve(), reuse_complete=args.reuse_complete)
    print(json.dumps({key: manifest[key] for key in ("visual_qa_status", "source_count", "page_count", "sheets")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
