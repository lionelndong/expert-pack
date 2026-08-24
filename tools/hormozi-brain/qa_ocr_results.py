#!/usr/bin/env python3
"""Classify rendered OCR pages for blank-page and manual-review triage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageStat


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("private-input/ocr-results"))
    args = parser.parse_args()
    reports = []
    for path in sorted((args.root / "reports").glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        for item in report.get("results", []):
            image_path = Path(str(item.get("qa_image", "")))
            try:
                with Image.open(image_path).convert("L") as image:
                    stats = ImageStat.Stat(image)
                    mean = float(stats.mean[0])
                    deviation = float(stats.stddev[0])
            except (OSError, ValueError):
                mean, deviation = None, None
            if int(item.get("word_count", 0)) == 0 and mean is not None and mean > 250 and deviation < 2:
                classification = "true_blank_page"
                manual_review = False
            elif int(item.get("word_count", 0)) == 0:
                classification = "graphic_or_ocr_failure_manual_review"
                manual_review = True
            elif item.get("needs_manual_review"):
                classification = "low_confidence_manual_review"
                manual_review = True
            else:
                classification = "ocr_text_present"
                manual_review = False
            item["image_mean_gray"] = mean
            item["image_stddev_gray"] = deviation
            item["classification"] = classification
            item["manual_review_required"] = manual_review
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        reports.append(report)
    all_items = [item for report in reports for item in report.get("results", [])]
    summary = {
        "pages": len(all_items),
        "true_blank_pages": sum(item.get("classification") == "true_blank_page" for item in all_items),
        "text_present": sum(item.get("classification") == "ocr_text_present" for item in all_items),
        "manual_review_pages": sum(bool(item.get("manual_review_required")) for item in all_items),
        "source_count": len(reports),
        "visual_qa_status": "rendered_and_blank_triaged_pending_manual_review",
        "reports": [str(args.root / "reports" / f"{report.get('source_id')}.json") for report in reports],
    }
    output = args.root / "qa-summary.json"
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    batch_path = args.root / "batch-report.json"
    if batch_path.is_file():
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        batch["completed_pages"] = len(all_items)
        batch["failed_sources"] = [] if len(all_items) == int(batch.get("requested_pages", len(all_items))) else batch.get("failed_sources", [])
        batch["manual_review_required"] = True
        batch_path.write_text(json.dumps(batch, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
