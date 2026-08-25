#!/usr/bin/env python3
"""Process the two restricted playbook records only after explicit authorization.

The command validates a machine-readable authorization record before reading
either PDF. With authorization, it hashes both copies, groups exact duplicates,
and optionally invokes the existing OCR/visual-QA adapter for each unique work.
Without authorization it exits before opening, hashing, OCRing, or embedding
the restricted files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESTRICTED_IDS = {"qsrc-6c6e3045f49e5555", "qsrc-01130c01e9dc5522"}
REQUIRED_AUTH_FIELDS = {"source_id", "rights_owner", "department", "obtained_via", "scope", "approver", "approved_at", "ticket"}


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read JSON record: {path}") from error
    if not isinstance(value, dict):
        raise TypeError(f"JSON record must be an object: {path}")
    return value


def validate_authorization(record: dict) -> list[str]:
    errors: list[str] = []
    if record.get("authorization_status") != "authorized":
        errors.append("authorization_status must be 'authorized'")
    if record.get("permitted_internal_processing") is not True:
        errors.append("permitted_internal_processing must be true")
    sources = record.get("sources")
    if not isinstance(sources, list):
        return errors + ["sources must be a list"]
    source_ids = [str(item.get("source_id", "")) for item in sources if isinstance(item, dict)]
    duplicate_ids = sorted({source_id for source_id in source_ids if source_ids.count(source_id) > 1 and source_id})
    if duplicate_ids:
        errors.append(f"duplicate authorization entries: {', '.join(duplicate_ids)}")
    unknown_ids = sorted(set(source_ids) - RESTRICTED_IDS)
    if unknown_ids:
        errors.append(f"authorization contains unknown sources: {', '.join(unknown_ids)}")
    if len(sources) != len(RESTRICTED_IDS):
        errors.append(f"authorization must contain exactly {len(RESTRICTED_IDS)} source entries")
    by_id = {str(item.get("source_id")): item for item in sources if isinstance(item, dict)}
    for source_id in sorted(RESTRICTED_IDS):
        item = by_id.get(source_id)
        if item is None:
            errors.append(f"missing authorization for {source_id}")
            continue
        missing = sorted(field for field in REQUIRED_AUTH_FIELDS if not str(item.get(field, "")).strip())
        if missing:
            errors.append(f"{source_id} missing fields: {', '.join(missing)}")
        placeholders = sorted(
            field
            for field in REQUIRED_AUTH_FIELDS
            if "REPLACE_WITH_" in str(item.get(field, ""))
        )
        if placeholders:
            errors.append(f"{source_id} still contains template placeholders: {', '.join(placeholders)}")
    return errors


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def source_records(manifest: dict) -> dict[str, dict]:
    records = {str(item.get("source_id")): item for item in manifest.get("quarantined_sources", []) if isinstance(item, dict)}
    missing = sorted(RESTRICTED_IDS - records.keys())
    if missing:
        raise ValueError(f"manifest is missing restricted records: {', '.join(missing)}")
    return {source_id: records[source_id] for source_id in sorted(RESTRICTED_IDS)}


def run_ocr(source_id: str, record: dict, pdf: Path, output_root: Path) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("pypdf is required to enumerate restricted PDF pages") from error
    page_count = len(PdfReader(str(pdf)).pages)
    if page_count < 1:
        raise RuntimeError(f"restricted PDF has no pages: {pdf}")
    source_output = output_root / source_id
    report_path = source_output / "ocr-report.json"
    command = [
        sys.executable,
        str(ROOT / "tools/hormozi-brain/ocr_sources.py"),
        "--pdf", str(pdf),
        "--pages", *[str(page) for page in range(1, page_count + 1)],
        "--output", str(source_output / "atoms"),
        "--source-id", source_id,
        "--relative-path", str(record.get("relative_path", "")),
        "--qa-dir", str(source_output / "visual-qa"),
        "--report", str(report_path),
    ]
    subprocess.run(command, check=True)
    return {"source_id": source_id, "pages": page_count, "report": str(report_path)}


def process(manifest_path: Path, authorization_path: Path, output: Path, run_ocr_flag: bool) -> dict:
    authorization = read_json(authorization_path)
    errors = validate_authorization(authorization)
    if errors:
        raise PermissionError("restricted processing refused: " + "; ".join(errors))
    records = source_records(read_json(manifest_path))
    hashed: list[dict] = []
    for source_id, record in records.items():
        pdf = Path(str(record.get("path", ""))).resolve()
        if not pdf.is_file():
            raise FileNotFoundError(f"authorized restricted source not found: {pdf}")
        hashed.append({"source_id": source_id, "path": str(pdf), "relative_path": record.get("relative_path"), "sha256": sha256_file(pdf), "size_bytes": pdf.stat().st_size})
    groups: dict[str, list[str]] = defaultdict(list)
    for item in hashed:
        groups[item["sha256"]].append(item["source_id"])
    unique = []
    for digest, source_ids in sorted(groups.items()):
        representative = next(item for item in hashed if item["sha256"] == digest)
        unique.append({"sha256": digest, "source_ids": sorted(source_ids), "representative_source_id": representative["source_id"], "duplicate": len(source_ids) > 1})
    ocr = []
    if run_ocr_flag:
        for group in unique:
            item = next(row for row in hashed if row["source_id"] == group["representative_source_id"])
            ocr.append(run_ocr(item["source_id"], records[item["source_id"]], Path(item["path"]), output / "ocr"))
    report = {
        "report_version": "1.0",
        "status": "authorized_processed",
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "authorization_record": str(authorization_path.resolve()),
        "sources": hashed,
        "duplicate_groups": unique,
        "unique_work_count": len(unique),
        "ocr_requested": run_ocr_flag,
        "ocr": ocr,
    }
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "restricted-source-resolution.json"
    report["report"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "private-input/inventory/hormozi-source-manifest.json")
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "private-input/restricted-processing")
    parser.add_argument("--run-ocr", action="store_true", help="After authorization, OCR each exact-unique PDF and retain visual-QA artifacts")
    args = parser.parse_args()
    try:
        report = process(args.manifest, args.authorization, args.output, args.run_ocr)
    except (PermissionError, FileNotFoundError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps({"status": report["status"], "unique_work_count": report["unique_work_count"], "ocr_requested": report["ocr_requested"], "report": report["report"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
