#!/usr/bin/env python3
"""Audit the complete Hormozi brain against the requested acceptance gates."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check(status: str, detail: str) -> dict[str, str]:
    return {"status": status, "detail": detail}


def read_json(path: Path, default: dict | None = None) -> dict:
    """Read an optional generated report without turning an incomplete rebuild into a crash."""

    if not path.is_file():
        return default.copy() if default else {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default.copy() if default else {}
    return value if isinstance(value, dict) else (default.copy() if default else {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "private-input/packs/alex-hormozi-brain-v1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    pack = args.pack.resolve()
    coverage = read_json(pack / "meta/brain-coverage.json")
    quality = read_json(pack / "meta/quality-report.json")
    estimate = read_json(pack / "meta/embedding-estimate.json")
    catalog = read_json(pack / "meta/official-channel-catalog.json")
    official_estimate = read_json(pack / "meta/official-transcription-estimate.json")
    container_path = pack / "meta/container-inspection.json"
    containers = read_json(container_path)
    readiness_path = pack / "meta/company-deployment-readiness.json"
    readiness = read_json(readiness_path)
    skill_validation_path = pack / "meta/skill-validation.json"
    skill_validation = read_json(skill_validation_path)
    decision_eval = read_json(pack / "meta/decision-support-evaluation.json")
    review_packet = read_json(ROOT / "private-input/ocr-results/manual-review/manual-review-manifest.json")
    skills_root = ROOT / "private-input/skills/alex-hormozi"
    inventory = [row for row in coverage.get("records", []) if row.get("kind") == "inventory_record"]
    restricted = [row for row in inventory if row.get("status") == "quarantined_restricted_authorization_required"]
    catalog_videos = [video for channel in catalog.get("channels", []) for video in channel.get("videos", [])]
    catalog_statuses = {}
    for video in catalog_videos:
        catalog_statuses[str(video.get("status"))] = catalog_statuses.get(str(video.get("status")), 0) + 1
    ocr = coverage.get("extras", {}).get("ocr", {})
    expected_manual_review_pages = sum(
        len(item.get("pages", []))
        for item in ocr.get("low_confidence_pages", [])
        if isinstance(item, dict)
    )
    packet_review_complete = (
        review_packet.get("visual_qa_status") == "manual_review_complete"
        and review_packet.get("page_count") == expected_manual_review_pages
        and review_packet.get("reviewed_count") == expected_manual_review_pages
        and review_packet.get("pending_count") == 0
    )
    ocr_visual_qa_complete = ocr.get("visual_qa_status") in {"complete", "manual_review_complete"} or packet_review_complete
    packages = sorted(path for path in skills_root.iterdir() if path.is_dir()) if skills_root.is_dir() else []
    missing_skills = [path.name for path in packages if not (path / "SKILL.md").is_file()]
    checks = {
        "inventory_ledger": check("pass" if len(inventory) == 428 and all(row.get("record_id") and row.get("status") for row in inventory) else "fail" if coverage else "pending", f"{len(inventory)} inventory records; every record has an ID and status"),
        "supplied_transcripts": check("pass" if coverage.get("transcripts", {}).get("unique_videos") == 273 and coverage.get("transcripts", {}).get("duplicate_sections_removed") == 3 else "fail" if coverage else "pending", f"{coverage.get('transcripts', {}).get('unique_videos')} unique videos; {coverage.get('transcripts', {}).get('transcript_atoms')} timestamped atoms"),
        "official_channel_coverage": check("pass" if len(catalog_videos) == 517 and set(catalog_statuses) <= {"already_present", "caption_ingested", "openai_transcribed", "caption_unavailable_pending_openai_transcription"} else "pending" if not catalog else "fail", f"{len(catalog_videos)} catalog videos; statuses={catalog_statuses}"),
        "official_transcription_estimate": check(
            "pass" if official_estimate.get("videos") and len(official_estimate.get("videos", [])) == 6 and official_estimate.get("metadata_available") is True and official_estimate.get("api_called") is False and official_estimate.get("media_downloaded") is False else "pending_external_metadata" if official_estimate.get("videos") and official_estimate.get("metadata_available") is False else "pending" if not official_estimate else "fail",
            f"{len(official_estimate.get('videos', []))} captionless videos; chunks={official_estimate.get('total_estimated_chunks')}; api_called={official_estimate.get('api_called')}; media_downloaded={official_estimate.get('media_downloaded')}",
        ),
        "paperclip_skills": check("pass" if len(packages) == 24 and not missing_skills and not coverage.get("skills", {}).get("invalid") and skill_validation.get("overall_status") == "pass" else "fail", f"{len(packages)} packages; structural={not missing_skills}; workflow_validation={skill_validation.get('overall_status', 'missing')}"),
        "ocr_pages": check(
            "pass" if ocr.get("requested_pages") == 442 and ocr.get("recovered_pages") == 442 and ocr_visual_qa_complete
            else "pending_manual_visual_qa" if ocr.get("requested_pages") == 442 and ocr.get("recovered_pages") == 442
            else "pending",
            f"{ocr.get('recovered_pages', 0)}/{ocr.get('requested_pages', 0)} pages recovered; coverage QA={ocr.get('visual_qa_status')}; review packet={review_packet.get('visual_qa_status', 'missing')} ({review_packet.get('reviewed_count', 0)}/{review_packet.get('page_count', expected_manual_review_pages)} reviewed)",
        ),
        "container_formats": check(
            "pass" if containers.get("source_count") == 3 and not containers.get("unique_knowledge_ingested") and all(
                row.get("status") in {"inspected_metadata_manifest_no_unique_knowledge", "inspected_container_manifest_no_unique_knowledge"}
                for row in containers.get("sources", [])
            ) else "fail",
            f"{containers.get('source_count', 0)} CSV/ZIP records inspected; unique knowledge ingested={containers.get('unique_knowledge_ingested', True)}",
        ),
        "restricted_sources": check("pending_external_authorization" if len(restricted) == 2 else "fail", f"{len(restricted)} restricted records remain quarantined"),
        "restricted_workflow": check(
            "pass" if (ROOT / "tools/hormozi-brain/process_restricted.py").is_file() and (ROOT / "config/source-intake/restricted-authorization.schema.json").is_file() else "fail",
            "fail-closed authorization, hash/deduplication, and optional OCR workflow is present",
        ),
        "audio": check("pending_external_api" if any(row.get("status") == "metadata_ready_pending_transcription" for row in coverage.get("extras", {}).get("audio", [])) else "pass", "Timestamped audio transcription requires OPENAI_API_KEY"),
        "embeddings": check("pending_external_api" if not os.environ.get("OPENAI_API_KEY") else "ready_to_run", f"{estimate.get('estimated_input_tokens')} estimated tokens; projected=${estimate.get('projected_embedding_cost_usd')}; local_model={estimate.get('local_model')}"),
        "offline_retrieval": check("pass" if quality.get("relevant_top5_rate") == 1.0 and quality.get("valid_citation_top5_rate") == 1.0 else "pending" if not quality else "fail", f"{quality.get('cases')} cases; top5 relevance={quality.get('relevant_top5_rate')}; locator validity={quality.get('valid_citation_top5_rate')}"),
        "decision_scenarios": check(
            "pass" if quality.get("decision_scenarios", {}).get("cases") == 20 and quality.get("decision_scenarios", {}).get("passed") == 20 else "pending" if not quality else "fail",
            f"{quality.get('decision_scenarios', {}).get('passed', 0)}/{quality.get('decision_scenarios', {}).get('cases', 0)} structural scenarios; live_agent_response_evaluation={quality.get('decision_scenarios', {}).get('live_agent_response_evaluation', 'missing')}",
        ),
        "live_agent_evaluation": check(
            "pass" if decision_eval.get("overall_status") == "pass" and decision_eval.get("expected_cases") == 20 and decision_eval.get("evaluated_cases") == 20 else "pending_agent_evaluation" if decision_eval.get("overall_status") == "pending_agent_responses" or not decision_eval else "fail",
            f"status={decision_eval.get('overall_status', 'not_run')}; responses={decision_eval.get('response_records', 0)}/{decision_eval.get('expected_cases', 0)}; live_agent_response_evaluation={decision_eval.get('live_agent_response_evaluation', False)}",
        ),
        "semantic_retrieval": check("pending_external_api" if quality.get("semantic_embedding_evaluation") == "not_run" else "pass", "Requires a completed OpenAI vector index and semantic benchmark"),
        "company_deployment": check(
            "pass" if readiness.get("overall_status") == "ready_for_gateway" else "staged" if readiness else "pending",
            f"Company preflight={readiness.get('overall_status', 'not_run')}; external gateway, TLS, identity policy, and distributed limits remain outside this workspace",
        ),
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
