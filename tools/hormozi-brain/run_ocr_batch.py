#!/usr/bin/env python3
"""Run the approved OCR page list without modifying source PDFs.

The evidence report is the authority for which pages may be processed. Results
and rendered QA images stay under ``private-input/ocr-results`` (gitignored).
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).with_name("ocr_sources.py")


def configure_tool_path() -> dict[str, str]:
    env = dict(os.environ)
    paths = [Path(r"C:\Program Files\Tesseract-OCR")]
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    paths.extend(winget.glob("oschwartz10612.Poppler_*/*/Library/bin"))
    paths.extend(winget.glob("oschwartz10612.Poppler_*/*/*/Library/bin"))
    env["PATH"] = os.pathsep.join(str(path) for path in paths if path.is_dir()) + os.pathsep + env.get("PATH", "")
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-report", type=Path, default=ROOT / "private-input/inventory/hormozi-evidence-build-report-v4.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / "private-input/inventory/hormozi-source-manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "private-input/ocr-results")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    evidence = json.loads(args.evidence_report.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest_by_id = {str(row["source_id"]): row for row in manifest.get("sources", [])}
    jobs = []
    for row in evidence.get("sources", []):
        pages = row.get("ocr_required_pages") or []
        source = manifest_by_id.get(str(row.get("source_id")))
        if not pages or not source:
            continue
        jobs.append({"source_id": str(row["source_id"]), "pages": [int(page) for page in pages], "pdf": str(source.get("path", "")), "relative_path": str(source.get("relative_path", ""))})
    args.output.mkdir(parents=True, exist_ok=True)
    env = configure_tool_path()
    missing = [tool for tool in ("pdftoppm", "tesseract") if shutil.which(tool, path=env.get("PATH")) is None]
    if missing:
        raise SystemExit("Missing OCR tools: " + ", ".join(missing))

    def run(job: dict[str, object]) -> dict[str, object]:
        report_path = args.output / "reports" / f"{job['source_id']}.json"
        command = [sys.executable, str(SCRIPT), "--pdf", str(job["pdf"]), "--pages", *[str(page) for page in job["pages"]], "--output", str(args.output / "atoms"), "--source-id", str(job["source_id"]), "--relative-path", str(job["relative_path"]), "--qa-dir", str(args.output / "qa" / str(job["source_id"])), "--report", str(report_path)]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, env=env, timeout=3600)
            return json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as error:  # keep every failed source visible in the batch report
            return {"source_id": job["source_id"], "pdf": job["pdf"], "pages_requested": job["pages"], "pages_completed": 0, "visual_qa_status": "failed", "error_type": type(error).__name__}

    reports = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(run, job) for job in jobs]
        for future in as_completed(futures):
            reports.append(future.result())
    reports.sort(key=lambda row: str(row.get("source_id")))
    batch = {
        "report_version": "1.0",
        "evidence_report": str(args.evidence_report),
        "source_count": len(jobs),
        "requested_pages": sum(len(job["pages"]) for job in jobs),
        "completed_pages": sum(int(row.get("pages_completed", 0)) for row in reports),
        "failed_sources": [row.get("source_id") for row in reports if row.get("visual_qa_status") == "failed"],
        "manual_review_required": True,
        "reports": reports,
    }
    output = args.output / "batch-report.json"
    output.write_text(json.dumps(batch, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: batch[key] for key in ("source_count", "requested_pages", "completed_pages", "failed_sources", "manual_review_required",)}, indent=2))
    return 0 if not batch["failed_sources"] and batch["completed_pages"] == batch["requested_pages"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
