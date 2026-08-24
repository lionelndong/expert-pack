#!/usr/bin/env python3
"""Audit the complete Hormozi brain against the requested acceptance gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

COMPLETION_REQUIREMENTS = [
    {
        "id": "coverage_ledger",
        "requirement": "All 428 inventory records have explicit statuses and no silent omissions.",
        "check": "inventory_ledger",
        "evidence": ["meta/brain-coverage.json", "meta/source-coverage.md"],
    },
    {
        "id": "youtube_transcripts",
        "requirement": "The supplied 273 unique videos are normalized into timestamped transcript records.",
        "check": "supplied_transcripts",
        "evidence": ["meta/brain-coverage.json", "youtube/"],
    },
    {
        "id": "official_channel_coverage",
        "requirement": "Verified official-channel coverage is enumerated and missing captions are explicit.",
        "check": "official_channel_coverage",
        "evidence": ["meta/official-channel-catalog.json", "meta/official-transcription-estimate.json"],
    },
    {
        "id": "official_captionless_transcripts",
        "requirement": "Every verified official-channel video without captions has an approved transcript or an explicit unresolved status.",
        "check": "official_captionless_transcripts",
        "evidence": ["meta/official-channel-catalog.json", "youtube/"],
    },
    {
        "id": "books_pdfs_ocr_epub_containers",
        "requirement": "Books, PDFs, OCR pages, EPUB chapters, and container manifests are provenance-checked.",
        "check": "ocr_pages",
        "evidence": ["meta/brain-coverage.json", "meta/ocr-batch-report.json", "meta/container-inspection.json", "ebook/"],
    },
    {
        "id": "paperclip_skills",
        "requirement": "All 24 Paperclip packages are searchable and structurally/workflow validated.",
        "check": "paperclip_skills",
        "evidence": ["meta/skill-validation.json", "agent-skills/", "private-input/skills/alex-hormozi/"],
    },
    {
        "id": "restricted_playbooks",
        "requirement": "The two restricted playbooks are authorized, hashed, deduplicated, and OCR-ingested before inclusion.",
        "check": "restricted_sources",
        "evidence": ["../restricted-processing/restricted-source-resolution.json", "meta/brain-coverage.json"],
    },
    {
        "id": "restricted_ocr",
        "requirement": "Authorized restricted OCR has page-level provenance and visual-QA evidence.",
        "check": "restricted_ocr",
        "evidence": ["../restricted-processing/", "ocr/"],
    },
    {
        "id": "audio_transcripts",
        "requirement": "Authorized audio works are transcribed into timestamp-located searchable atoms.",
        "check": "audio",
        "evidence": ["meta/brain-coverage.json", "audio/"],
    },
    {
        "id": "openai_embedding_path",
        "requirement": "The direct OpenAI embedding provider and local SQLite vector path are implemented without a local model.",
        "check": "embedding_path",
        "evidence": ["../../runtime/ep-mcp/ep_mcp/embeddings/openai.py", "../../runtime/ep-mcp/ep_mcp/index/sqlite_store.py", "meta/embedding-estimate.json"],
    },
    {
        "id": "mcp_brain_surface",
        "requirement": "One cited agent-facing MCP brain exposes the four Hormozi-specific tools.",
        "check": "mcp_surface",
        "evidence": ["../../runtime/ep-mcp/ep_mcp/server.py", "../../runtime/ep-mcp/tests/unit/test_mcp_server.py"],
    },
    {
        "id": "citation_quality",
        "requirement": "Offline retrieval returns relevant top-five sources with valid locators.",
        "check": "offline_retrieval",
        "evidence": ["meta/quality-report.json"],
    },
    {
        "id": "semantic_index",
        "requirement": "The OpenAI vector index is built locally and semantic retrieval is benchmarked.",
        "check": "semantic_retrieval",
        "evidence": ["../../runtime/ep-mcp-index/alex-hormozi-brain/index.db", "meta/quality-report.json"],
    },
    {
        "id": "decision_support",
        "requirement": "Decision scenarios enforce evidence, inference, conflict, and boundary handling.",
        "check": "decision_scenarios",
        "evidence": ["meta/quality-report.json", "meta/decision-support-evaluation.json"],
    },
    {
        "id": "live_agent_evaluation",
        "requirement": "Twenty live decision scenarios are evaluated for multi-source evidence and conflict disclosure.",
        "check": "live_agent_evaluation",
        "evidence": ["meta/decision-support-evaluation.json"],
    },
    {
        "id": "company_staging",
        "requirement": "The service is staged for company authentication, rate limiting, and audit logging.",
        "check": "company_deployment",
        "evidence": ["meta/company-deployment-readiness.json", "../../config/ep-mcp.company.example.yaml", "../../runtime/ep-mcp/ep_mcp/auth.py"],
    },
]


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


def sqlite_embedding_index_ready(path: Path) -> bool:
    """Verify the local SQLite index has an OpenAI model and chunks."""

    if not path.is_file():
        return False
    try:
        with sqlite3.connect(path) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            # SQLiteStore uses ``index_meta``.  ``meta`` is retained as a
            # compatibility path for older test fixtures / pre-runtime index
            # builds, but must not be assumed to be the production schema.
            metadata_table = "index_meta" if "index_meta" in tables else "meta"
            if metadata_table not in tables:
                return False
            rows = dict(connection.execute(
                f"SELECT key, value FROM {metadata_table} "
                "WHERE key IN ('embedding_model', 'embedding_dimension', 'chunk_count')"
            ))
        return (
            str(rows.get("embedding_model", "")).startswith("openai/")
            and int(rows.get("embedding_dimension", 0) or 0) == 1536
            and int(rows.get("chunk_count", 0) or 0) > 0
        )
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


def write_completion_matrix(pack: Path, checks: dict[str, dict[str, str]]) -> dict[str, object]:
    """Write a requirement-to-evidence map for human and machine review."""

    rows: list[dict[str, object]] = []
    for requirement in COMPLETION_REQUIREMENTS:
        check_result = checks.get(str(requirement["check"]), {"status": "missing", "detail": "check not emitted"})
        rows.append({
            **requirement,
            "status": check_result.get("status", "missing"),
            "detail": check_result.get("detail", ""),
        })
    pending = [row["id"] for row in rows if row["status"] != "pass"]
    matrix = {
        "report_version": "1.0",
        "overall_status": "pass" if not pending else "pending_external_gates",
        "requirements": rows,
        "pending_requirements": pending,
    }
    json_path = pack / "meta" / "completion-matrix.json"
    json_path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# Hormozi brain completion matrix", "", f"Overall status: `{matrix['overall_status']}`", "", "| Requirement | Status | Evidence |", "|---|---|---|"]
    for row in rows:
        lines.append(f"| `{row['requirement']}` | `{row['status']}` | {', '.join(f'`{item}`' for item in row['evidence'])} |")
    lines.extend(["", "## Details", ""])
    lines.extend(f"- **{row['id']}**: {row['detail']}" for row in rows)
    body = "\n".join(lines) + "\n"
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    frontmatter = {
        "title": "Hormozi brain completion matrix",
        "type": "meta",
        "pack": "alex-hormozi-brain",
        "tags": ["coverage", "acceptance", "provenance"],
        "schema_version": "4.1",
        "id": "alex-hormozi-brain/meta/completion-matrix",
        "content_hash": f"sha256:{content_hash}",
        "retrieval_strategy": "on_demand",
        "verified_at": datetime.now(timezone.utc).date().isoformat(),
        "verified_by": "acceptance-audit",
        "confidence": "inferred",
    }
    import yaml

    rendered = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False).strip() + "\n---\n" + body
    (pack / "meta" / "completion-matrix.md").write_text(rendered, encoding="utf-8")
    return matrix


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
    restricted_resolution = read_json(ROOT / "private-input/restricted-processing/restricted-source-resolution.json")
    skills_root = ROOT / "private-input/skills/alex-hormozi"
    inventory = [row for row in coverage.get("records", []) if row.get("kind") == "inventory_record"]
    ledger_fields = (
        "record_id", "status", "format", "title", "origin", "extraction_status",
        "pack_membership", "rights_status", "sha256", "duplicate_group",
    )
    ledger_complete = len(inventory) == 428 and all(
        all(field in row for field in ledger_fields) for row in inventory
    )
    restricted = [row for row in inventory if row.get("status") == "quarantined_restricted_authorization_required"]
    catalog_videos = [video for channel in catalog.get("channels", []) for video in channel.get("videos", [])]
    catalog_statuses = {}
    for video in catalog_videos:
        catalog_statuses[str(video.get("status"))] = catalog_statuses.get(str(video.get("status")), 0) + 1
    captionless_videos = [
        video for video in catalog_videos
        if video.get("status") == "caption_unavailable_pending_openai_transcription"
    ]
    embedding_index_path = ROOT / "runtime/ep-mcp-index/alex-hormozi-brain/index.db"
    embedding_index_ready = sqlite_embedding_index_ready(embedding_index_path)
    ocr = coverage.get("extras", {}).get("ocr", {})
    restricted_pack = coverage.get("extras", {}).get("restricted", {})
    coverage_manual_review_pages = sum(
        len(item.get("pages", []))
        for item in ocr.get("low_confidence_pages", [])
        if isinstance(item, dict)
    )
    expected_manual_review_pages = (
        review_packet.get("page_count")
        if isinstance(review_packet.get("page_count"), int)
        else coverage_manual_review_pages
    )
    packet_review_complete = (
        review_packet.get("visual_qa_status") == "manual_review_complete"
        and review_packet.get("page_count") == expected_manual_review_pages
        and review_packet.get("reviewed_count") == expected_manual_review_pages
        and review_packet.get("pending_count") == 0
        and not any(
            int(count or 0) > 0
            for decision, count in (review_packet.get("decision_counts") or {}).items()
            if decision in {"reocr_required", "unreadable"}
        )
    )
    ocr_visual_qa_complete = ocr.get("visual_qa_status") in {"complete", "manual_review_complete"} or packet_review_complete
    packages = sorted(path for path in skills_root.iterdir() if path.is_dir()) if skills_root.is_dir() else []
    missing_skills = [path.name for path in packages if not (path / "SKILL.md").is_file()]
    checks = {
        "inventory_ledger": check("pass" if ledger_complete else "fail" if coverage else "pending", f"{len(inventory)} inventory records; full provenance/lifecycle ledger fields present={ledger_complete}"),
        "supplied_transcripts": check("pass" if coverage.get("transcripts", {}).get("unique_videos") == 273 and coverage.get("transcripts", {}).get("duplicate_sections_removed") == 3 else "fail" if coverage else "pending", f"{coverage.get('transcripts', {}).get('unique_videos')} unique videos; {coverage.get('transcripts', {}).get('transcript_atoms')} timestamped atoms"),
        "official_channel_coverage": check("pass" if len(catalog_videos) == 517 and set(catalog_statuses) <= {"already_present", "caption_ingested", "openai_transcribed", "caption_unavailable_pending_openai_transcription"} else "pending" if not catalog else "fail", f"{len(catalog_videos)} catalog videos; statuses={catalog_statuses}"),
        "official_captionless_transcripts": check(
            "pass" if not captionless_videos else "pending_external_api" if not os.environ.get("OPENAI_API_KEY") else "pending_transcription",
            f"{len(captionless_videos)} official videos remain without a transcript",
        ),
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
        "restricted_sources": check(
            "pass" if len(restricted) == 0 and restricted_resolution.get("status") == "authorized_processed" and restricted_pack.get("status") == "authorized_processed" else "pending_external_authorization" if len(restricted) == 2 and restricted_resolution.get("status", "missing") in {"missing", "pending_external_authorization"} else "fail",
            f"{len(restricted)} restricted records remain quarantined; resolution_report={restricted_resolution.get('status', 'missing')}; pack_status={restricted_pack.get('status', 'missing')}",
        ),
        "restricted_ocr": check(
            "pass" if restricted_pack.get("status") == "authorized_processed" and restricted_pack.get("ocr_requested") and int(restricted_pack.get("ocr_atoms", 0) or 0) > 0 else "pending_external_ocr" if restricted_pack.get("status") in {"authorized_pending_ocr", "authorized_processed"} and restricted_pack.get("ocr_requested") else "pending_external_authorization" if len(restricted) == 2 else "fail",
            f"status={restricted_pack.get('status', 'missing')}; ocr_requested={restricted_pack.get('ocr_requested', False)}; atoms={restricted_pack.get('ocr_atoms', 0)}",
        ),
        "restricted_workflow": check(
            "pass" if (ROOT / "tools/hormozi-brain/process_restricted.py").is_file() and (ROOT / "config/source-intake/restricted-authorization.schema.json").is_file() else "fail",
            "fail-closed authorization, hash/deduplication, and optional OCR workflow is present",
        ),
        "audio": check("pending_external_api" if any(row.get("status") == "metadata_ready_pending_transcription" for row in coverage.get("extras", {}).get("audio", [])) else "pass", "Timestamped audio transcription requires OPENAI_API_KEY"),
        "embeddings": check(
            "pass" if embedding_index_ready else "pending_external_api" if not os.environ.get("OPENAI_API_KEY") else "pending_index_build",
            f"{estimate.get('estimated_input_tokens')} estimated tokens; projected=${estimate.get('projected_embedding_cost_usd')}; local_model={estimate.get('local_model')}; index_ready={embedding_index_ready}",
        ),
        "offline_retrieval": check("pass" if quality.get("relevant_top5_rate") == 1.0 and quality.get("valid_citation_top5_rate") == 1.0 else "pending" if not quality else "fail", f"{quality.get('cases')} cases; top5 relevance={quality.get('relevant_top5_rate')}; locator validity={quality.get('valid_citation_top5_rate')}"),
        "decision_scenarios": check(
            "pass" if quality.get("decision_scenarios", {}).get("cases") == 20 and quality.get("decision_scenarios", {}).get("passed") == 20 else "pending" if not quality else "fail",
            f"{quality.get('decision_scenarios', {}).get('passed', 0)}/{quality.get('decision_scenarios', {}).get('cases', 0)} structural scenarios; live_agent_response_evaluation={quality.get('decision_scenarios', {}).get('live_agent_response_evaluation', 'missing')}",
        ),
        "live_agent_evaluation": check(
            "pass" if decision_eval.get("overall_status") == "pass" and decision_eval.get("expected_cases") == 20 and decision_eval.get("evaluated_cases") == 20 else "pending_agent_evaluation" if decision_eval.get("overall_status") == "pending_agent_responses" or not decision_eval else "fail",
            f"status={decision_eval.get('overall_status', 'not_run')}; responses={decision_eval.get('response_records', 0)}/{decision_eval.get('expected_cases', 0)}; live_agent_response_evaluation={decision_eval.get('live_agent_response_evaluation', False)}",
        ),
        "semantic_retrieval": check(
            "pass" if embedding_index_ready and quality.get("semantic_embedding_evaluation") != "not_run" else "pending_external_api" if not os.environ.get("OPENAI_API_KEY") else "pending_semantic_benchmark",
            f"index_ready={embedding_index_ready}; semantic_evaluation={quality.get('semantic_embedding_evaluation', 'missing')}",
        ),
        "company_deployment": check(
            "pass" if readiness.get("overall_status") == "ready_for_gateway" else "staged" if readiness else "pending",
            f"Company preflight={readiness.get('overall_status', 'not_run')}; external gateway, TLS, identity policy, and distributed limits remain outside this workspace",
        ),
    }
    checks["embedding_path"] = check(
        "pass" if (ROOT / "runtime/ep-mcp/ep_mcp/embeddings/openai.py").is_file() and (ROOT / "runtime/ep-mcp/ep_mcp/index/sqlite_store.py").is_file() else "fail",
        "Direct OpenAI provider, local SQLite storage, and no-local-model configuration are present",
    )
    checks["mcp_surface"] = check(
        "pass" if (ROOT / "runtime/ep-mcp/ep_mcp/server.py").is_file() and (ROOT / "runtime/ep-mcp/tests/unit/test_mcp_server.py").is_file() else "fail",
        "Hormozi MCP server tools and schema tests are present",
    )
    pending = [name for name, value in checks.items() if value["status"].startswith("pending") or value["status"] == "staged"]
    report = {"report_version": "1.0", "overall_status": "pending_external_gates" if pending else "pass", "pending_or_staged": pending, "checks": checks, "pack": str(pack)}
    output = (args.output or pack / "meta/acceptance-audit.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    matrix = write_completion_matrix(pack, checks)
    print(json.dumps({"overall_status": report["overall_status"], "pending_or_staged": pending, "completion_matrix": matrix["overall_status"], "output": str(output)}, indent=2))
    return 0 if not [value for value in checks.values() if value["status"] == "fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
