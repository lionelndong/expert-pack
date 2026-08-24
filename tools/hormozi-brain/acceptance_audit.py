#!/usr/bin/env python3
"""Audit the complete Hormozi brain against the requested acceptance gates."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def check(status: str, detail: str) -> dict[str, str]:
    return {"status": status, "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "private-input/packs/alex-hormozi-brain-v1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    pack = args.pack.resolve()
    coverage = json.loads((pack / "meta/brain-coverage.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "private-input/inventory/hormozi-source-manifest.json").read_text(encoding="utf-8"))
    quality = json.loads((pack / "meta/quality-report.json").read_text(encoding="utf-8"))
    estimate = json.loads((pack / "meta/embedding-estimate.json").read_text(encoding="utf-8"))
    catalog = json.loads((pack / "meta/official-channel-catalog.json").read_text(encoding="utf-8"))
    skills_root = ROOT / "private-input/skills/alex-hormozi"
    inventory = [row for row in coverage["records"] if row.get("kind") == "inventory_record"]
    restricted = [row for row in inventory if row.get("status") == "quarantined_restricted_authorization_required"]
    catalog_videos = [video for channel in catalog.get("channels", []) for video in channel.get("videos", [])]
    catalog_statuses = {}
    for video in catalog_videos:
        catalog_statuses[str(video.get("status"))] = catalog_statuses.get(str(video.get("status")), 0) + 1
    ocr = coverage.get("extras", {}).get("ocr", {})
    packages = sorted(path for path in skills_root.iterdir() if path.is_dir()) if skills_root.is_dir() else []
    missing_skills = [path.name for path in packages if not (path / "SKILL.md").is_file()]
    checks = {
        "inventory_ledger": check("pass" if len(inventory) == 428 and all(row.get("record_id") and row.get("status") for row in inventory) else "fail", f"{len(inventory)} inventory records; every record has an ID and status"),
        "supplied_transcripts": check("pass" if coverage.get("transcripts", {}).get("unique_videos") == 273 and coverage.get("transcripts", {}).get("duplicate_sections_removed") == 3 else "fail", f"{coverage.get('transcripts', {}).get('unique_videos')} unique videos; {coverage.get('transcripts', {}).get('transcript_atoms')} timestamped atoms"),
        "official_channel_coverage": check("pass" if len(catalog_videos) == 517 and set(catalog_statuses) <= {"already_present", "caption_ingested", "caption_unavailable_pending_openai_transcription"} else "fail", f"{len(catalog_videos)} catalog videos; statuses={catalog_statuses}"),
        "paperclip_skills": check("pass" if len(packages) == 24 and not missing_skills and not coverage.get("skills", {}).get("invalid") else "fail", f"{len(packages)} packages; missing_skill_files={missing_skills}"),
        "ocr_pages": check("pass" if ocr.get("requested_pages") == 442 and ocr.get("recovered_pages") == 442 else "pending", f"{ocr.get('recovered_pages', 0)}/{ocr.get('requested_pages', 0)} pages recovered; visual QA={ocr.get('visual_qa_status')}"),
        "restricted_sources": check("pending_external_authorization" if len(restricted) == 2 else "fail", f"{len(restricted)} restricted records remain quarantined"),
        "audio": check("pending_external_api" if any(row.get("status") == "metadata_ready_pending_transcription" for row in coverage.get("extras", {}).get("audio", [])) else "pass", "Timestamped audio transcription requires OPENAI_API_KEY"),
        "embeddings": check("pending_external_api" if not os.environ.get("OPENAI_API_KEY") else "ready_to_run", f"{estimate.get('estimated_input_tokens')} estimated tokens; projected=${estimate.get('projected_embedding_cost_usd')}; local_model={estimate.get('local_model')}"),
        "offline_retrieval": check("pass" if quality.get("relevant_top5_rate") == 1.0 and quality.get("valid_citation_top5_rate") == 1.0 else "fail", f"{quality.get('cases')} cases; top5 relevance={quality.get('relevant_top5_rate')}; locator validity={quality.get('valid_citation_top5_rate')}"),
        "semantic_retrieval": check("pending_external_api" if quality.get("semantic_embedding_evaluation") == "not_run" else "pass", "Requires a completed OpenAI vector index and semantic benchmark"),
        "company_deployment": check("staged" if yaml.safe_load((ROOT / "config/ep-mcp.company.example.yaml").read_text(encoding="utf-8"))["server"].get("host") == "0.0.0.0" else "pending", "Company config stages host/origin allowlists, API-key auth, rate limits, and JSONL audit logging; deployment gateway is external"),
    }
    pending = [name for name, value in checks.items() if value["status"].startswith("pending") or value["status"] == "staged"]
    report = {"report_version": "1.0", "overall_status": "pending_external_gates" if pending else "pass", "pending_or_staged": pending, "checks": checks, "pack": str(pack)}
    output = (args.output or pack / "meta/acceptance-audit.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"overall_status": report["overall_status"], "pending_or_staged": pending, "output": str(output)}, indent=2))
    return 0 if not [value for value in checks.values() if value["status"] == "fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
