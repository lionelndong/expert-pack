#!/usr/bin/env python3
"""Build a private composite Alex Hormozi ExpertPack.

The builder keeps raw/private sources outside Git, refuses inventory-quarantined
records, creates structured YouTube atoms, mirrors approved packs and Paperclip
skills, and writes a coverage ledger with explicit pending statuses.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "private-input/packs/alex-hormozi-brain-v1"
DEFAULT_MANIFEST = ROOT / "private-input/inventory/hormozi-source-manifest.json"
DEFAULT_EVIDENCE = ROOT / "private-input/inventory/hormozi-evidence-build-report-v4.json"
DEFAULT_SKILLS_REPORT = ROOT / "private-input/inventory/hormozi-skills-build-report-v2.json"
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


def audio_metadata(path: Path, output: Path, ledger: list[dict[str, object]]) -> dict[str, object]:
    target = output / "audio"
    target.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {"path": str(path), "status": "pending_openai_transcription"}
    try:
        completed = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size", "-of", "json", str(path)], check=True, capture_output=True, text=True)
        result["metadata"] = json.loads(completed.stdout).get("format", {})
        result["status"] = "metadata_ready_pending_transcription"
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        result["error"] = str(error)
    (target / f"{slug(path.stem)}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ledger.append({"record_id": f"derived-audio-{slug(path.stem)}", "kind": "derived_audio", "title": path.stem, "status": result["status"], "source_file": str(path), "pack_membership": "audio"})
    return result


def make_ledger(manifest: dict, evidence: dict, skill_report: dict) -> list[dict[str, object]]:
    evidence_by_id = {str(row["source_id"]): row for row in evidence.get("sources", [])}
    skills_by_id = {str(row["source_id"]): row for row in skill_report.get("sources", [])}
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
            elif evidence_row.get("action") == "excluded_by_rights_decision":
                status = "excluded_by_rights_or_scope"
            else:
                status = "pending_rights_or_quality_review"
            records.append({
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
            })
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        if record.get("sha256"):
            groups[str(record["sha256"])].append(record)
    for group in groups.values():
        group.sort(key=lambda item: str(item["record_id"]))
        for duplicate in group[1:]:
            duplicate["duplicate_of"] = group[0]["record_id"]
            duplicate["status"] = "duplicate_by_sha256"
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
            "retrieval_strategy": "standard",
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
        "context": {"always": ["overview.md", "STATUS.md"], "searchable": ["evidence/", "curated-skills/", "youtube/", "ebook/", "agent-skills/"], "on_demand": ["meta/"]},
    }
    (output / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    (output / "overview.md").write_text("# Alex Hormozi — Complete Internal Brain\n\n> Source-grounded decision support. This corpus synthesizes retrieved evidence; it does not claim to be Alex Hormozi or speak for him.\n\n## Surfaces\n\n" + f"- {transcript_report['unique_videos']} structured YouTube transcript records.\n- {counts.get('evidence', 0)} approved evidence atoms.\n- {counts.get('curated-skills', 0)} curated skill-source atoms.\n- {len(skill_report.get('packages', []))} executable Paperclip skill packages.\n- EPUB/audio/OCR/channel gaps are explicit in `meta/brain-coverage.json`.\n", encoding="utf-8", newline="\n")
    for directory in ("evidence", "curated-skills", "youtube", "ebook", "agent-skills", "meta"):
        index_path = output / directory / "_index.md"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(f"# {directory.replace('-', ' ').title()}\n\nGenerated navigation index for the private Hormozi brain.\n", encoding="utf-8", newline="\n")
    meta = output / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    report = {"report_version": "1.0", "generated_at": "2026-08-23", "inventory_records": inventory_count, "derived_records": len(ledger) - inventory_count, "summary": dict(Counter(str(row.get("status")) for row in ledger)), "transcripts": transcript_report, "skills": skill_report, "extras": extras, "records": ledger}
    (meta / "brain-coverage.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    status_lines = ["# Brain coverage report", "", f"Inventory records: {inventory_count}", f"Derived records: {len(ledger) - inventory_count}", "", "## Status counts", "", "| Status | Count |", "|---|---:|"]
    status_lines += [f"| `{name}` | {count} |" for name, count in sorted(report["summary"].items())]
    status_lines += ["", "## Explicit pending items", "", "- The two `LEAKED_Pricing_Playbook.pdf` records remain quarantined pending documented authorization.", "- OCR, audio transcription, and official-channel enumeration are never silently treated as complete.", "- Every inventory record has a status and duplicate relationship where a SHA-256 is available."]
    coverage_body = "\n".join(status_lines) + "\n"
    coverage_fm = {"title": "Brain coverage report", "type": "meta", "pack": "alex-hormozi-brain", "tags": ["coverage", "provenance"], "schema_version": "4.1", "id": "alex-hormozi-brain/meta/source-coverage", "content_hash": sha256_text(coverage_body), "retrieval_strategy": "on_demand", "verified_at": "2026-08-23", "verified_by": "brain-builder", "confidence": "crawled"}
    (meta / "source-coverage.md").write_text("---\n" + yaml.safe_dump(coverage_fm, sort_keys=False, allow_unicode=True).strip() + "\n---\n" + coverage_body, encoding="utf-8", newline="\n")
    (output / "STATUS.md").write_text("# Brain status\n\n- Inventory records: " + str(inventory_count) + "\n- Structured YouTube videos: " + str(transcript_report["unique_videos"]) + "\n- Executable skill packages: " + str(len(skill_report.get("packages", []))) + "\n- Restricted playbooks: quarantined pending authorization\n- OpenAI embedding index: pending `OPENAI_API_KEY`\n- Official-channel expansion: pending explicit yt-dlp acquisition\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence-report", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--skills-report", type=Path, default=DEFAULT_SKILLS_REPORT)
    parser.add_argument("--no-epub", action="store_true")
    parser.add_argument("--no-audio-metadata", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    private_root = (ROOT / "private-input").resolve()
    if private_root not in output.parents:
        raise SystemExit("output must be inside private-input")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence_report.read_text(encoding="utf-8"))
    skill_sources = json.loads(args.skills_report.read_text(encoding="utf-8"))
    ledger = make_ledger(manifest, evidence, skill_sources)
    counts = {"evidence": copy_md(ROOT / "private-input/packs/alex-hormozi-evidence-v4/concepts", output / "evidence"), "curated-skills": copy_md(ROOT / "private-input/packs/alex-hormozi-skills-v2/concepts", output / "curated-skills")}
    transcript_report = build_transcripts(output, ledger)
    skill_report = copy_skills(output)
    extras: dict[str, object] = {"ebook": [], "audio": [], "ocr": {"status": "pending_external_ocr_tooling", "pages": 442}}
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
    write_pack(output, ledger, transcript_report, skill_report, counts, extras)
    print(json.dumps({"output": str(output), "inventory_records": sum(row.get("kind") == "inventory_record" for row in ledger), "derived_records": sum(row.get("kind") != "inventory_record" for row in ledger), "transcripts": transcript_report, "packs": counts, "skills": skill_report, "extras": extras}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
