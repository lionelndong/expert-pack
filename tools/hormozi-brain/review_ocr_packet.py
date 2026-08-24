#!/usr/bin/env python3
"""Record explicit visual decisions for the OCR review packet.

This command is intentionally fail-closed: a packet only becomes
``manual_review_complete`` after every low-confidence page has a valid,
reviewer-supplied decision. It never infers a decision from OCR confidence.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MANIFEST = Path("private-input/ocr-results/manual-review/manual-review-manifest.json")
VALID_DECISIONS = {"accept_ocr", "reocr_required", "graphic_or_blank", "unreadable"}


def _load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"OCR review manifest not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"OCR review manifest is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("OCR review manifest must contain a JSON object")
    return value


def validate_manifest(manifest: dict) -> dict:
    pages = manifest.get("pages")
    if not isinstance(pages, list):
        raise ValueError("OCR review manifest must contain a pages array")
    declared_count = manifest.get("page_count")
    if declared_count != len(pages):
        raise ValueError(f"page_count={declared_count} does not match pages={len(pages)}")
    seen: set[str] = set()
    reviewed = 0
    decision_counts = {decision: 0 for decision in VALID_DECISIONS}
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("Every OCR review page must be an object")
        review_id = str(page.get("review_id") or "")
        if not review_id:
            raise ValueError("Every OCR review page must have a review_id")
        if review_id in seen:
            raise ValueError(f"Duplicate OCR review_id: {review_id}")
        seen.add(review_id)
        status = page.get("review_status", "pending")
        decision = page.get("review_decision")
        if status not in {"pending", "reviewed"}:
            raise ValueError(f"Invalid review_status for {review_id}: {status}")
        if status == "reviewed":
            if decision not in VALID_DECISIONS:
                raise ValueError(f"Reviewed page {review_id} has invalid decision: {decision}")
            reviewed += 1
            decision_counts[decision] += 1
        elif decision is not None:
            raise ValueError(f"Pending page {review_id} cannot have a decision")
    pending = len(pages) - reviewed
    manifest["reviewed_count"] = reviewed
    manifest["pending_count"] = pending
    manifest["decision_counts"] = decision_counts
    manifest["visual_qa_status"] = "manual_review_complete" if pages and pending == 0 else "pending_manual_review"
    return {
        "page_count": len(pages),
        "reviewed_count": reviewed,
        "pending_count": pending,
        "decision_counts": decision_counts,
        "visual_qa_status": manifest["visual_qa_status"],
    }


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(path)


def record_decision(
    manifest: dict,
    review_id: str,
    decision: str,
    notes: str | None = None,
    *,
    allow_change: bool = False,
) -> dict:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"Invalid decision {decision!r}; choose one of {sorted(VALID_DECISIONS)}")
    matches = [page for page in manifest.get("pages", []) if page.get("review_id") == review_id]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one page for review_id={review_id!r}; found {len(matches)}")
    page = matches[0]
    existing = page.get("review_decision")
    if existing and existing != decision and not allow_change:
        raise ValueError(
            f"{review_id} already has decision {existing!r}; pass --allow-change to replace it"
        )
    page["review_status"] = "reviewed"
    page["review_decision"] = decision
    page["review_notes"] = notes
    page["reviewed_at_utc"] = datetime.now(timezone.utc).isoformat()
    return validate_manifest(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--review-id", help="Stable page ID, for example src-abc:page-0001")
    parser.add_argument("--decision", choices=sorted(VALID_DECISIONS))
    parser.add_argument("--notes")
    parser.add_argument("--allow-change", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    path = args.manifest.resolve()
    manifest = _load_manifest(path)
    if args.validate_only:
        summary = validate_manifest(manifest)
    else:
        if not args.review_id or not args.decision:
            parser.error("--review-id and --decision are required unless --validate-only is used")
        summary = record_decision(
            manifest,
            args.review_id,
            args.decision,
            args.notes,
            allow_change=args.allow_change,
        )
        _atomic_write(path, manifest)
    print(json.dumps({"manifest": str(path), **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
