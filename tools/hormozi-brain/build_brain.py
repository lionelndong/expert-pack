#!/usr/bin/env python3
"""Build a private composite Alex Hormozi ExpertPack.

The builder keeps raw/private sources outside Git, refuses inventory-quarantined
records, creates structured YouTube atoms, mirrors approved packs and Paperclip
skills, and writes a coverage ledger with explicit pending statuses.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "private-input/packs/alex-hormozi-brain-v1"
DEFAULT_MANIFEST = ROOT / "private-input/inventory/hormozi-source-manifest.json"
DEFAULT_EVIDENCE = ROOT / "private-input/inventory/hormozi-evidence-build-report-v4.json"
DEFAULT_SKILLS_REPORT = ROOT / "private-input/inventory/hormozi-skills-build-report-v2.json"
DEFAULT_OCR_RESULTS = ROOT / "private-input/ocr-results"
VIDEO_HEADER = re.compile(r"^(.+?)\s+-\s+YouTube\s*$", re.I)
VIDEO_URL = re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=[^\s]+", re.I)
TIMESTAMP = re.compile(r"^\((\d{1,2}):(\d{2})(?::(\d{2}))?\)\s*(.*)$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def slug(value: str, fallback: str = "source") -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result or fallback


def find_transcripts() -> list[Path]:
    explicit = [Path(item) for item in os.environ.get("HORMOZI_SOURCE_ROOTS", "").split(os.pathsep) if item]
    roots = explicit + [Path.home() / "Downloads" / "ALEX HORMOZI"]
    found: set[Path] = set()
    for root in roots:
        if root.is_dir():
            found.update(path.resolve() for path in root.rglob("YT_Transcripts_*.txt"))
    return sorted(found)


def find_paperclip() -> Path | None:
    candidates = []
    if os.environ.get("HORMOZI_PAPERCLIP_ROOT"):
        candidates.append(Path(os.environ["HORMOZI_PAPERCLIP_ROOT"]))
    candidates.append(Path.home() / "Downloads" / "paperclip.ai" / "alex-hormozi-skills")
    return next((path.resolve() for path in candidates if path.is_dir()), None)


def parse_sections(path: Path) -> list[dict[str, object]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    result: list[dict[str, object]] = []
    index = 0
    while index < len(lines):
        header = VIDEO_HEADER.match(lines[index].strip())
        if not header:
            index += 1
            continue
        url_index = next((i for i in range(index + 1, min(index + 8, len(lines))) if VIDEO_URL.search(lines[i])), None)
        if url_index is None:
            index += 1
            continue
        url = VIDEO_URL.search(lines[url_index]).group(0).rstrip(")>,")
        marker = next((i for i in range(url_index + 1, min(url_index + 8, len(lines))) if lines[i].strip().casefold() == "transcript:"), None)
        if marker is None:
            index = url_index + 1
            continue
        end = marker + 1
        while end < len(lines) and not VIDEO_HEADER.match(lines[end].strip()):
            end += 1
        video_id = parse_qs(urlparse(url).query).get("v", [""])[0]
        if video_id:
            result.append({
                "title": header.group(1).strip(),
                "url": url,
                "video_id": video_id,
                "transcript": "\n".join(lines[marker + 1:end]).strip(),
                "source_file": str(path),
                "source_line_start": marker + 2,
                "source_line_end": max(marker + 1, end),
            })
        index = end
    return result


def timestamp_seconds(line: str) -> int | None:
    match = TIMESTAMP.match(line.strip())
    if not match:
        return None
    hour_or_minute, minute_or_second, second = match.groups()[:3]
    if second is None:
        return int(hour_or_minute) * 60 + int(minute_or_second)
    return int(hour_or_minute) * 3600 + int(minute_or_second) * 60 + int(second)


def transcript_markdown(section: dict[str, object]) -> str:
    title = str(section["title"]).replace("[[", "[\u200b[")
    transcript = str(section["transcript"]).replace("[[", "[\u200b[")
    video_id = str(section["video_id"])
    observed = [timestamp_seconds(line) for line in transcript.splitlines()]
    observed = [value for value in observed if value is not None]
    part_label = f" — part {section.get('part_index')}" if section.get("part_index") else ""
    body = (
        f"# {title}{part_label}\n\n"
        "> Evidence boundary: transcript-derived source material for decision support; "
        "not a current statement, endorsement, or impersonation of Alex Hormozi.\n\n"
        "## Provenance\n\n"
        f"- Video ID: `{video_id}`\n- YouTube URL: {section['url']}\n"
        f"- Transcript file: `{section['source_file']}`\n"
        f"- Source lines: {section['source_line_start']}-{section['source_line_end']}\n"
        f"- Transcript part: {section.get('part_index', 1)}/{section.get('part_count', 1)}\n"
        f"- Timestamp range: {section.get('start_timestamp')}s-{section.get('end_timestamp')}s\n"
        f"- Latest observed timestamp: {max(observed) if observed else None}s\n\n"
        "## Transcript\n\n```text\n" + transcript + "\n```\n"
    )
    frontmatter = {
        "title": title,
        "type": "reference",
        "pack": "alex-hormozi-brain",
        "tags": ["youtube-transcript", "public-source", "alex-hormozi", "timestamped"],
        "schema_version": "4.1",
        "id": f"alex-hormozi-brain/youtube/{video_id}/part-{int(section.get('part_index', 1)):03d}",
        "content_hash": sha256_text(body),
        "verified_at": "2026-08-23",
        "verified_by": "transcript-normalizer",
        "confidence": "crawled",
        "retrieval_strategy": "standard",
    }
    return "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip() + "\n---\n" + body


def build_transcripts(output: Path, ledger: list[dict[str, object]]) -> dict[str, object]:
    target = output / "youtube"
    target.mkdir(parents=True, exist_ok=True)
    sections: dict[str, dict[str, object]] = {}
    total = 0
    duplicates = 0
    files = find_transcripts()
    for source_file in files:
        for section in parse_sections(source_file):
            total += 1
            video_id = str(section["video_id"])
            if video_id in sections:
                duplicates += 1
            else:
                sections[video_id] = section
    atom_count = 0
    for video_id, section in sorted(sections.items()):
        lines = str(section["transcript"]).splitlines()
        chunks: list[list[str]] = []
        current: list[str] = []
        current_chars = 0
        for line in lines:
            if current and current_chars + len(line) + 1 > 4500 and TIMESTAMP.match(line.strip()):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(line)
            current_chars += len(line) + 1
        if current:
            chunks.append(current)
        chunks = chunks or [[]]
        for part_index, chunk_lines in enumerate(chunks, start=1):
            part = dict(section)
            part["transcript"] = "\n".join(chunk_lines).strip()
            part["part_index"] = part_index
            part["part_count"] = len(chunks)
            timestamps = [timestamp_seconds(line) for line in chunk_lines]
            timestamps = [value for value in timestamps if value is not None]
            part["start_timestamp"] = min(timestamps) if timestamps else None
            part["end_timestamp"] = max(timestamps) if timestamps else None
            path = target / f"{slug(str(section['title']), 'video')}-{video_id}-part-{part_index:03d}.md"
            path.write_text(transcript_markdown(part), encoding="utf-8", newline="\n")
            atom_count += 1
        ledger.append({
            "record_id": f"youtube-{video_id}",
            "kind": "derived_youtube_video",
            "title": section["title"],
            "status": "included_structured_transcript",
            "source_url": section["url"],
            "video_id": video_id,
            "source_file": section["source_file"],
            "locator": f"lines {section['source_line_start']}-{section['source_line_end']}",
            "pack_membership": "youtube",
            "atom_count": len(chunks),
        })
    return {
        "source_files": [str(path) for path in files],
        "sections_found": total,
        "unique_videos": len(sections),
        "duplicate_sections_removed": duplicates,
        "transcript_atoms": atom_count,
        "official_channel_enumeration": "pending_ytdlp_and_channel_verification",
    }


def merge_preserved_official(output: Path, preserved: Path | None, ledger: list[dict[str, object]], transcript_report: dict[str, object]) -> None:
    """Restore caption atoms acquired by fetch_official after a rebuild."""
    if preserved is None:
        return
    youtube_source = preserved / "youtube"
    youtube_target = output / "youtube"
    youtube_target.mkdir(parents=True, exist_ok=True)
    existing_ids = {str(row.get("video_id")) for row in ledger if row.get("video_id")}
    catalog_statuses: dict[str, str] = {}
    catalog_path = preserved / "meta" / "official-channel-catalog.json"
    if catalog_path.is_file():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog_statuses = {
                str(video.get("video_id")): str(video.get("status", "caption_ingested"))
                for channel in catalog.get("channels", [])
                for video in channel.get("videos", [])
                if video.get("video_id")
            }
        except (OSError, json.JSONDecodeError, TypeError):
            catalog_statuses = {}
    added = 0
    for path in sorted(youtube_source.glob("*.md")) if youtube_source.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "official:" not in text:
            continue
        match = re.search(r"-([A-Za-z0-9_-]{11})-part-\d{3}\.md$", path.name)
        if not match:
            continue
        video_id = match.group(1)
        destination = youtube_target / path.name
        if not destination.exists():
            shutil.copy2(path, destination)
        if video_id not in existing_ids:
            catalog_status = catalog_statuses.get(video_id, "caption_ingested")
            derived_status = "included_official_transcription" if catalog_status == "openai_transcribed" else "included_official_caption"
            ledger.append({"record_id": f"youtube-{video_id}", "kind": "derived_youtube_video", "title": path.stem, "status": derived_status, "video_id": video_id, "source_file": text.split("Transcript file:", 1)[-1].splitlines()[0].strip(" `") if "Transcript file:" in text else "official-channel", "pack_membership": "youtube", "transcription_provider": "openai" if catalog_status == "openai_transcribed" else None})
            existing_ids.add(video_id)
            added += 1
    if catalog_path.is_file():
        output_meta = output / "meta"
        output_meta.mkdir(parents=True, exist_ok=True)
        shutil.copy2(catalog_path, output_meta / catalog_path.name)
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            videos = [video for channel in catalog.get("channels", []) for video in channel.get("videos", [])]
            transcript_report["official_channel_video_count"] = len(videos)
            transcript_report["official_catalog_status_counts"] = dict(Counter(str(video.get("status", "unknown")) for video in videos))
        except (OSError, json.JSONDecodeError, TypeError):
            transcript_report["official_catalog_status_counts"] = {"catalog_read_error": 1}
    transcript_report["official_caption_videos"] = added
    transcript_report["official_channel_enumeration"] = "verified_channel_catalog_present"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() in {"script", "style", "nav"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "nav"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(html.unescape(data.strip()))


def extract_epub(path: Path, output: Path, ledger: list[dict[str, object]]) -> dict[str, object]:
    target = output / "ebook"
    target.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {"path": str(path), "status": "extraction_error", "chapters": 0}
    try:
        with zipfile.ZipFile(path) as archive:
            sections: list[tuple[str, str]] = []
            for name in archive.namelist():
                if not name.casefold().endswith((".xhtml", ".html", ".htm")):
                    continue
                parser = TextExtractor()
                parser.feed(archive.read(name).decode("utf-8", errors="replace"))
                text = "\n".join(parser.parts).strip()
                if text:
                    sections.append((name, text))
            if sections:
                outputs: list[str] = []
                for index, (name, text) in enumerate(sections, start=1):
                    body = f"# $100M Money Models — EPUB chapter {index}\n\n## EPUB path\n\n`{name}`\n\n{text}\n"
                    path_out = target / f"100m-money-models-chapter-{index:03d}.md"
                    fm = {"title": f"$100M Money Models — EPUB chapter {index}", "type": "reference", "pack": "alex-hormozi-brain", "tags": ["ebook", "money-models", "chapter"], "schema_version": "4.1", "id": f"alex-hormozi-brain/ebook/100m-money-models/chapter-{index:03d}", "content_hash": sha256_text(body), "retrieval_strategy": "standard", "verified_at": "2026-08-23", "confidence": "crawled", "verified_by": "epub-extractor"}
                    path_out.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip() + "\n---\n" + body, encoding="utf-8", newline="\n")
                    outputs.append(str(path_out))
                result.update(status="included_extracted", chapters=len(sections), output=outputs)
            else:
                result["status"] = "extracted_empty"
    except (OSError, zipfile.BadZipFile) as error:
        result["error"] = str(error)
    ledger.append({"record_id": "derived-ebook-100m-money-models", "kind": "derived_ebook", "title": "$100M Money Models EPUB", "status": result["status"], "source_file": str(path), "pack_membership": "ebook"})
    return result


def inspect_containers(manifest: dict[str, object]) -> dict[str, object]:
    """Inspect approved CSV/ZIP containers without ingesting duplicate raw content."""
    sources = list(manifest.get("sources", [])) + list(manifest.get("quarantined_sources", []))
    report_rows: list[dict[str, object]] = []
    csv_rows: list[tuple[str, list[dict[str, str]], list[str]]] = []
    for source in sources:
        source_type = str(source.get("type", ""))
        path = Path(str(source.get("path", "")))
        if source_type == "csv" and path.is_file():
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = [{str(key): str(value or "").strip() for key, value in row.items()} for row in reader]
                fieldnames = [str(value) for value in (reader.fieldnames or [])]
            csv_rows.append((str(source["source_id"]), rows, fieldnames))
            report_rows.append({
                "source_id": str(source["source_id"]),
                "type": source_type,
                "source_hash": source.get("hash"),
                "path": source.get("relative_path"),
                "status": "inspected_metadata_manifest_no_unique_knowledge",
                "row_count": len(rows),
                "fieldnames": fieldnames,
                "unique_knowledge_ingested": False,
                "reason": "CSV contains library metadata already represented by the canonical inventory and derived pack records.",
            })
        elif source_type == "archive" and path.is_file():
            with zipfile.ZipFile(path) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
            manifest_matches = 0
            unmatched: list[str] = []
            for member in members:
                member_name = str(member.filename).replace("\\", "/")
                matched = any(
                    int(row.get("size_bytes", -1)) == member.file_size
                    and (
                        str(row.get("relative_path", "")).replace("\\", "/").endswith(member_name)
                        or Path(str(row.get("relative_path", ""))).name == Path(member_name).name
                    )
                    for row in sources
                )
                if matched:
                    manifest_matches += 1
                else:
                    unmatched.append(member_name)
            status = "inspected_container_manifest_no_unique_knowledge" if not unmatched else "inspected_container_unmatched_members"
            report_rows.append({
                "source_id": str(source["source_id"]),
                "type": source_type,
                "source_hash": source.get("hash"),
                "path": source.get("relative_path"),
                "status": status,
                "member_count": len(members),
                "manifest_member_matches": manifest_matches,
                "unmatched_members": unmatched,
                "unique_knowledge_ingested": bool(unmatched),
                "reason": "ZIP members were inspected as a container manifest; matching members are already represented by inventoried sources.",
            })
    normalized_groups: dict[str, list[str]] = defaultdict(list)
    for source_id, rows, fieldnames in csv_rows:
        normalized = json.dumps(sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)), ensure_ascii=False, sort_keys=True)
        normalized_groups[hashlib.sha256(normalized.encode("utf-8")).hexdigest()].append(source_id)
    for row in report_rows:
        if row["type"] == "csv":
            source_id = str(row["source_id"])
            row["duplicate_group"] = next((group for group in normalized_groups.values() if source_id in group), [source_id])
    return {
        "status": "complete",
        "source_count": len(report_rows),
        "sources": report_rows,
        "unique_knowledge_ingested": any(bool(row.get("unique_knowledge_ingested")) for row in report_rows),
    }


def audio_metadata(path: Path, output: Path, ledger: list[dict[str, object]]) -> dict[str, object]:
    target = output / "audio"
    target.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {"path": str(path), "status": "pending_openai_transcription"}
    try:
        completed = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size", "-of", "json", str(path)], check=True, capture_output=True, text=True)
        result["metadata"] = json.loads(completed.stdout).get("format", {})
        result["status"] = "metadata_ready_pending_transcription"
        duration = float(result["metadata"].get("duration", 0) or 0)
        chunk_seconds = 600
        result["transcription_plan"] = {
            "model": "gpt-4o-mini-transcribe",
            "chunk_seconds": chunk_seconds,
            "chunk_count": max(0, (int(duration + chunk_seconds - 1) // chunk_seconds)),
            "timestamped_segments": True,
            "requires_openai_api_key": True,
        }
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        result["error"] = str(error)
    (target / f"{slug(path.stem)}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ledger.append({"record_id": f"derived-audio-{slug(path.stem)}", "kind": "derived_audio", "title": path.stem, "status": result["status"], "source_file": str(path), "pack_membership": "audio"})
    return result


def make_ledger(manifest: dict, evidence: dict, skill_report: dict, container_report: dict | None = None) -> list[dict[str, object]]:
    evidence_by_id = {str(row["source_id"]): row for row in evidence.get("sources", [])}
    skills_by_id = {str(row["source_id"]): row for row in skill_report.get("sources", [])}
    containers_by_id = {str(row["source_id"]): row for row in (container_report or {}).get("sources", [])}
    records: list[dict[str, object]] = []
    for group in ("sources", "quarantined_sources", "unreadable_sources"):
        for source in manifest.get(group, []):
            source_id = str(source["source_id"])
            evidence_row = evidence_by_id.get(source_id, {})
            skill_row = skills_by_id.get(source_id, {})
            if group == "quarantined_sources":
                status = "quarantined_restricted_authorization_required"
            elif evidence_row.get("action") in {"extracted", "extracted_with_ocr_required_pages"}:
                status = "indexed_text_layer_ocr_pending" if evidence_row.get("ocr_required_pages") else "indexed_evidence"
            elif skill_row.get("action") == "extracted":
                status = "indexed_curated_skill_source"
            elif evidence_row.get("action") == "unsupported_type_reported":
                status = "approved_but_format_pending"
            elif evidence_row.get("action") == "deduplicated_by_source_hash":
                status = "duplicate_by_sha256"
            elif evidence_row.get("action") == "excluded_by_rights_decision":
                status = "excluded_by_rights_or_scope"
            else:
                status = "pending_rights_or_quality_review"
            container_row = containers_by_id.get(source_id)
            if container_row and container_row.get("status") in {
                "inspected_metadata_manifest_no_unique_knowledge",
                "inspected_container_manifest_no_unique_knowledge",
            }:
                status = str(container_row["status"])
            record = {
                "record_id": source_id,
                "kind": "inventory_record",
                "relative_path": source.get("relative_path"),
                "absolute_path": source.get("path"),
                "format": source.get("type"),
                "sha256": source.get("hash"),
                "rights_status": source.get("rights_status"),
                "use_scope": source.get("use_scope"),
                "status": status,
                "evidence_action": evidence_row.get("action"),
                "skills_action": skill_row.get("action"),
                "ocr_required_pages": evidence_row.get("ocr_required_pages", []),
                "pack_membership": [name for name, row in (("evidence", evidence_row), ("curated-skills", skill_row)) if row.get("action") in {"extracted", "extracted_with_ocr_required_pages"}],
            }
            if container_row:
                record["container_inspection"] = container_row["status"]
            if evidence_row.get("action") == "deduplicated_by_source_hash" and evidence_row.get("duplicate_of"):
                record["duplicate_of"] = str(evidence_row["duplicate_of"])
            records.append(record)
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        if record.get("sha256"):
            groups[str(record["sha256"])].append(record)

    def canonical_id(record_id: str, by_id: dict[str, dict[str, object]]) -> str:
        """Follow evidence-level duplicate links to the terminal source."""
        seen: set[str] = set()
        current = record_id
        while current in by_id and current not in seen and by_id[current].get("duplicate_of"):
            seen.add(current)
            current = str(by_id[current]["duplicate_of"])
        return current

    status_priority = {
        "indexed_ocr_recovered_pending_manual_visual_qa": 0,
        "indexed_evidence": 0,
        "indexed_curated_skill_source": 0,
        "approved_but_format_pending": 2,
        "pending_rights_or_quality_review": 3,
        "excluded_by_rights_or_scope": 4,
        "quarantined_restricted_authorization_required": 5,
    }
    by_id = {str(record["record_id"]): record for record in records}
    for group in groups.values():
        explicit_targets = {
            canonical_id(str(record["duplicate_of"]), by_id)
            for record in group
            if record.get("duplicate_of") and str(record["duplicate_of"]) in by_id
        }
        if explicit_targets:
            winner_id = min(explicit_targets)
        else:
            winner_id = min(
                (str(record["record_id"]) for record in group),
                key=lambda record_id: (status_priority.get(str(by_id[record_id].get("status")), 9), record_id),
            )
        for record in group:
            record_id = str(record["record_id"])
            if record_id == winner_id:
                record.pop("duplicate_of", None)
                continue
            record["duplicate_of"] = winner_id
            record["status"] = "duplicate_by_sha256"
    return records


def copy_md(source: Path, destination: Path) -> int:
    if not source.is_dir():
        return 0
    count = 0
    for path in source.rglob("*.md"):
        if path.name == "_index.md":
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def integrate_ocr(output: Path, ocr_root: Path, ledger: list[dict[str, object]]) -> dict[str, object]:
    """Copy rendered OCR atoms and reconcile page status with the ledger."""
    result: dict[str, object] = {
        "status": "pending_external_ocr_tooling",
        "requested_pages": 0,
        "recovered_pages": 0,
        "source_count": 0,
        "low_confidence_pages": [],
        "true_blank_pages": [],
        "visual_qa_status": "not_started",
        "results_dir": str(ocr_root),
    }
    if not ocr_root.is_dir():
        return result
    report_files = sorted((ocr_root / "reports").glob("*.json")) if (ocr_root / "reports").is_dir() else []
    reports: list[dict[str, object]] = []
    for report_path in report_files:
        try:
            reports.append(json.loads(report_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    result["source_count"] = len(reports)
    result["requested_pages"] = sum(int(row.get("pages_requested", 0) if isinstance(row.get("pages_requested"), int) else len(row.get("pages_requested", []))) for row in reports)
    result["recovered_pages"] = sum(int(row.get("pages_completed", 0)) for row in reports)
    result["low_confidence_pages"] = [
        {"source_id": row.get("source_id"), "pages": row.get("low_confidence_pages", [])}
        for row in reports
        if row.get("low_confidence_pages")
    ]
    if reports:
        result["visual_qa_status"] = "rendered_pending_manual_review"
    qa_lookup: dict[tuple[str, int], str] = {}
    for report in reports:
        for item in report.get("results", []):
            try:
                key = (str(item.get("source_id")), int(item.get("page")))
            except (TypeError, ValueError):
                continue
            qa_lookup[key] = str(item.get("classification", ""))
    qa_summary_path = ocr_root / "qa-summary.json"
    if qa_summary_path.is_file():
        try:
            result["visual_qa_status"] = json.loads(qa_summary_path.read_text(encoding="utf-8")).get("visual_qa_status", result["visual_qa_status"])
        except (OSError, json.JSONDecodeError):
            pass
    if result["requested_pages"] and result["recovered_pages"] == result["requested_pages"]:
        result["status"] = "indexed_ocr_recovered_pending_manual_visual_qa"
    elif result["recovered_pages"]:
        result["status"] = "partially_recovered_ocr_pending"

    atoms = sorted((ocr_root / "atoms").glob("*.md")) if (ocr_root / "atoms").is_dir() else []
    meta_target = output / "meta"
    meta_target.mkdir(parents=True, exist_ok=True)
    for report_name in ("batch-report.json", "qa-summary.json"):
        report_path = ocr_root / report_name
        if report_path.is_file():
            shutil.copy2(report_path, meta_target / f"ocr-{report_name}")
    target = output / "ocr"
    copied_by_source: dict[str, list[dict[str, object]]] = defaultdict(list)
    for atom in atoms:
        text = atom.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^source_id:\s*([^\n]+)", text, re.M)
        page_match = re.search(r"^source_page:\s*(\d+)", text, re.M)
        source_id = match.group(1).strip().strip("'") if match else "unknown"
        page = int(page_match.group(1)) if page_match else None
        classification = qa_lookup.get((source_id, page), "") if page is not None else ""
        destination = target / atom.name
        if classification != "true_blank_page":
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(atom, destination)
        else:
            result["true_blank_pages"].append({"source_id": source_id, "page": page})
        copied_by_source[source_id].append({"path": destination, "page": page, "title": atom.stem})
        ledger.append({"record_id": f"ocr-{source_id}-page-{page:04d}" if page is not None else f"ocr-{source_id}-{atom.stem}", "kind": "derived_ocr_page", "title": atom.stem, "status": "ocr_true_blank_page" if classification == "true_blank_page" else ("included_ocr_page_manual_review" if classification else "included_ocr_page"), "source_id": source_id, "source_page": page, "source_file": str(atom), "pack_membership": "ocr"})
    for row in ledger:
        if row.get("kind") != "inventory_record" or not row.get("ocr_required_pages"):
            continue
        recovered = sorted(int(item["page"]) for item in copied_by_source.get(str(row.get("record_id")), []) if item.get("page") is not None)
        expected = sorted(int(page) for page in row.get("ocr_required_pages", []))
        if recovered:
            row["ocr_recovered_pages"] = recovered
            row["ocr_manual_visual_review"] = True
            if recovered == expected:
                row["status"] = "indexed_ocr_recovered_pending_manual_visual_qa"
            else:
                row["status"] = "indexed_ocr_partial_pending"
    return result


def copy_skills(output: Path) -> dict[str, object]:
    source = find_paperclip()
    result: dict[str, object] = {"status": "missing", "packages": [], "invalid": []}
    if source is None:
        return result
    pack_destination = output / "agent-skills"
    mirror_destination = ROOT / "private-input/skills/alex-hormozi"
    pack_destination.mkdir(parents=True, exist_ok=True)
    mirror_destination.mkdir(parents=True, exist_ok=True)
    for package in sorted(path for path in source.iterdir() if path.is_dir()):
        skill_file = package / "SKILL.md"
        if not skill_file.is_file():
            result["invalid"].append(package.name)
            continue
        content = skill_file.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"^name:\s*[^\n]+", content, re.M) or not re.search(r"^description:\s*[^\n]+", content, re.M):
            result["invalid"].append(package.name)
            continue
        target = mirror_destination / package.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(package, target)

        # The full package is executable skill material, not a native
        # ExpertPack atom.  Expose one validated entrypoint atom in the MCP
        # corpus and retain all supporting chapters/scripts in the private
        # mirror above.
        raw = skill_file.read_text(encoding="utf-8", errors="replace")
        body = raw
        source_description = package.name
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) == 3:
                try:
                    source_description = str((yaml.safe_load(parts[1]) or {}).get("description") or package.name)
                except yaml.YAMLError:
                    source_description = package.name
                body = parts[2].lstrip()
        # Keep the MCP entrypoint concise; the full executable package is
        # preserved in private-input/skills and is returned on demand.
        if "## Chapter Index" in body:
            body = body.split("## Chapter Index", 1)[0].rstrip()
        body = body[:4200].rstrip()
        atom_body = (
            f"# {package.name}\n\n"
            f"{source_description}\n\n"
            "This executable Paperclip skill is available to the local agent runtime. "
            "Use its supporting package files for chapter-level detail, and cite the "
            "retrieved evidence atoms when applying it.\n\n"
            + body
            + "\n\n## Private package\n\n"
            f"`private-input/skills/alex-hormozi/{package.name}/`\n"
        )
        frontmatter = {
            "title": package.name,
            "type": "workflow",
            "pack": "alex-hormozi-brain",
            "tags": ["agent-skill", "paperclip", "alex-hormozi", package.name],
            "schema_version": "4.1",
            "id": f"alex-hormozi-brain/agent-skills/{package.name}",
            "content_hash": sha256_text(atom_body),
            "verified_at": "2026-08-23",
            "verified_by": "paperclip-skill-normalizer",
            "confidence": "crawled",
            "related": ["overview.md"],
            "retrieval_strategy": "atomic",
        }
        (pack_destination / f"{package.name}.md").write_text(
            "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip() + "\n---\n" + atom_body,
            encoding="utf-8",
            newline="\n",
        )
        result["packages"].append(package.name)
    result.update(status="ready", source=str(source), package_root=str(mirror_destination))
    return result


def write_pack(output: Path, ledger: list[dict[str, object]], transcript_report: dict[str, object], skill_report: dict[str, object], counts: dict[str, int], extras: dict[str, object]) -> None:
    inventory_count = sum(row.get("kind") == "inventory_record" for row in ledger)
    included_count = sum(
        row.get("kind") == "inventory_record"
        and str(row.get("status", "")).startswith("indexed")
        for row in ledger
    )
    manifest = {
        "name": "Alex Hormozi — Complete Internal Brain",
        "slug": "alex-hormozi-brain",
        # This is one physical ExpertPack exposed through one MCP endpoint.
        # The content is composite (evidence + transcripts + skills), but the
        # single-pack type lets the strict validator inspect every atom.
        "type": "person",
        "version": "1.0.0",
        "schema_version": "4.1",
        "retrieval_model": "retrieval-first",
        "description": "Private provenance-first composite pack combining approved evidence, structured YouTube transcripts, and executable Paperclip skills.",
        "entry_point": "overview.md",
        "author": "Alex Hormozi Brain project",
        "created": "2026-08-23",
        "updated": "2026-08-23",
        "freshness": {"refresh_cycle": "P30D", "last_full_review": "2026-08-23", "verified_file_count": included_count, "total_file_count": inventory_count, "coverage_pct": round(included_count / inventory_count * 100, 2) if inventory_count else 0},
        "authority_boundary": {"in_scope": "Evidence-backed frameworks and decision patterns represented by retrieved approved or public source records.", "out_of_scope": ["Claims without a supporting atom", "Current personal opinions, endorsements, or authorization by Alex Hormozi", "Quarantined, unauthorized, excluded, or unresolved sources", "Legal, tax, investment, medical, or regulated advice"], "refuse_when": ["No supporting source exists", "Source rights or provenance are unresolved"], "no_source_no_claim": True},
        "context": {"always": ["overview.md", "STATUS.md"], "searchable": ["evidence/", "curated-skills/", "youtube/", "ebook/", "ocr/", "agent-skills/"], "on_demand": ["meta/"]},
    }
    (output / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    (output / "overview.md").write_text("# Alex Hormozi — Complete Internal Brain\n\n> Source-grounded decision support. This corpus synthesizes retrieved evidence; it does not claim to be Alex Hormozi or speak for him.\n\n## Surfaces\n\n" + f"- {transcript_report['unique_videos']} structured YouTube transcript records.\n- {counts.get('evidence', 0)} approved evidence atoms.\n- {counts.get('curated-skills', 0)} curated skill-source atoms.\n- {len(skill_report.get('packages', []))} executable Paperclip skill packages.\n- OCR/audio/channel gaps are explicit in `meta/brain-coverage.json`.\n", encoding="utf-8", newline="\n")
    for directory in ("evidence", "curated-skills", "youtube", "ebook", "ocr", "agent-skills", "meta"):
        index_path = output / directory / "_index.md"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(f"# {directory.replace('-', ' ').title()}\n\nGenerated navigation index for the private Hormozi brain.\n", encoding="utf-8", newline="\n")
    meta = output / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    report = {"report_version": "1.0", "generated_at": "2026-08-23", "inventory_records": inventory_count, "derived_records": len(ledger) - inventory_count, "summary": dict(Counter(str(row.get("status")) for row in ledger)), "transcripts": transcript_report, "skills": skill_report, "extras": extras, "records": ledger}
    (meta / "brain-coverage.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if extras.get("containers"):
        (meta / "container-inspection.json").write_text(json.dumps(extras["containers"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    status_lines = ["# Brain coverage report", "", f"Inventory records: {inventory_count}", f"Derived records: {len(ledger) - inventory_count}", "", "## Status counts", "", "| Status | Count |", "|---|---:|"]
    status_lines += [f"| `{name}` | {count} |" for name, count in sorted(report["summary"].items())]
    status_lines += ["", "## Explicit pending items", "", "- The two `LEAKED_Pricing_Playbook.pdf` records remain quarantined pending documented authorization.", "- OCR, audio transcription, and official-channel enumeration are never silently treated as complete.", "- Every inventory record has a status and duplicate relationship where a SHA-256 is available."]
    coverage_body = "\n".join(status_lines) + "\n"
    coverage_fm = {"title": "Brain coverage report", "type": "meta", "pack": "alex-hormozi-brain", "tags": ["coverage", "provenance"], "schema_version": "4.1", "id": "alex-hormozi-brain/meta/source-coverage", "content_hash": sha256_text(coverage_body), "retrieval_strategy": "on_demand", "verified_at": "2026-08-23", "verified_by": "brain-builder", "confidence": "crawled"}
    (meta / "source-coverage.md").write_text("---\n" + yaml.safe_dump(coverage_fm, sort_keys=False, allow_unicode=True).strip() + "\n---\n" + coverage_body, encoding="utf-8", newline="\n")
    official_status = transcript_report.get("official_catalog_status_counts", {})
    official_line = "verified catalog present" if official_status else "pending explicit yt-dlp acquisition"
    ocr_status = extras.get("ocr", {})
    ocr_line = f"{ocr_status.get('recovered_pages', 0)}/{ocr_status.get('requested_pages', 0)} pages recovered; visual QA remains explicit"
    (output / "STATUS.md").write_text("# Brain status\n\n- Inventory records: " + str(inventory_count) + "\n- Structured YouTube videos: " + str(transcript_report["unique_videos"]) + "\n- Official-channel catalog videos: " + str(transcript_report.get("official_channel_video_count", "unknown")) + "\n- Official-channel caption records: " + str(transcript_report.get("official_caption_videos", 0)) + "\n- OCR: " + ocr_line + "\n- Executable skill packages: " + str(len(skill_report.get("packages", []))) + "\n- Restricted playbooks: quarantined pending authorization\n- OpenAI embedding index: pending `OPENAI_API_KEY`\n- Official-channel expansion: " + official_line + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence-report", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--skills-report", type=Path, default=DEFAULT_SKILLS_REPORT)
    parser.add_argument("--ocr-results", type=Path, default=DEFAULT_OCR_RESULTS)
    parser.add_argument("--no-epub", action="store_true")
    parser.add_argument("--no-audio-metadata", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    private_root = (ROOT / "private-input").resolve()
    if private_root not in output.parents:
        raise SystemExit("output must be inside private-input")
    preserved: Path | None = None
    if output.exists():
        preserved = Path(tempfile.mkdtemp(prefix="hormozi-official-preserve-"))
        existing_youtube = output / "youtube"
        if existing_youtube.is_dir():
            (preserved / "youtube").mkdir(parents=True, exist_ok=True)
            for path in existing_youtube.glob("*.md"):
                text = path.read_text(encoding="utf-8", errors="replace")
                if "official:" in text:
                    shutil.copy2(path, preserved / "youtube" / path.name)
        catalog = output / "meta" / "official-channel-catalog.json"
        if catalog.is_file():
            (preserved / "meta").mkdir(parents=True, exist_ok=True)
            shutil.copy2(catalog, preserved / "meta" / catalog.name)
        shutil.rmtree(output)
    output.mkdir(parents=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence_report.read_text(encoding="utf-8"))
    skill_sources = json.loads(args.skills_report.read_text(encoding="utf-8"))
    container_report = inspect_containers(manifest)
    ledger = make_ledger(manifest, evidence, skill_sources, container_report)
    counts = {"evidence": copy_md(ROOT / "private-input/packs/alex-hormozi-evidence-v4/concepts", output / "evidence"), "curated-skills": copy_md(ROOT / "private-input/packs/alex-hormozi-skills-v2/concepts", output / "curated-skills")}
    transcript_report = build_transcripts(output, ledger)
    merge_preserved_official(output, preserved, ledger, transcript_report)
    skill_report = copy_skills(output)
    extras: dict[str, object] = {"ebook": [], "audio": [], "containers": container_report, "ocr": integrate_ocr(output, args.ocr_results, ledger)}
    seen_hashes: set[str] = set()
    for source in manifest.get("sources", []):
        path = Path(str(source.get("path", "")))
        if not path.is_file() or source.get("rights_status") == "excluded":
            continue
        if path.suffix.casefold() in {".epub", ".mp3", ".wav", ".m4a", ".flac"}:
            digest = str(source.get("hash") or sha256_file(path))
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            if path.suffix.casefold() == ".epub":
                extras["ebook"].append(extract_epub(path, output, ledger))
            elif not args.no_audio_metadata:
                extras["audio"].append(audio_metadata(path, output, ledger))
    for ebook_result in extras["ebook"]:
        if ebook_result.get("status") != "included_extracted":
            continue
        for record in ledger:
            if record.get("kind") == "inventory_record" and record.get("absolute_path") == ebook_result.get("path"):
                record["status"] = "included_extracted"
                record["pack_membership"] = sorted(set(record.get("pack_membership", [])) | {"ebook"})
    write_pack(output, ledger, transcript_report, skill_report, counts, extras)
    if preserved is not None:
        # The preservation staging directory contains private transcript text;
        # remove it after the new pack is written.
        shutil.rmtree(preserved, ignore_errors=True)
    print(json.dumps({"output": str(output), "inventory_records": sum(row.get("kind") == "inventory_record" for row in ledger), "derived_records": sum(row.get("kind") != "inventory_record" for row in ledger), "transcripts": transcript_report, "packs": counts, "skills": skill_report, "extras": extras}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
