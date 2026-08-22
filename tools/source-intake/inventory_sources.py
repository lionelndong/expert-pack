#!/usr/bin/env python3
"""Create a provenance manifest from explicitly approved source roots.

This is deliberately an intake-only tool. It walks files, gathers filesystem
metadata, and reads byte streams solely to calculate SHA-256 hashes. It never
copies, converts, OCRs, parses, transcribes, extracts, or indexes source
contents. Paths matching default suspect terms are quarantined before hashing;
only a metadata-only audit record is emitted for them.

Example:
    python tools/source-intake/inventory_sources.py \
      --approved-root "C:\\approved\\hormozi-library" \
      --out "C:\\safe-output\\source-manifest.json"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Iterable
import uuid


MANIFEST_VERSION = "1.0"
DEFAULT_SUSPECT_TERMS = ("leaked", "pirated", "crack")
HASH_CHUNK_BYTES = 1024 * 1024

EXTENSION_TYPES = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".rtf": "document",
    ".doc": "document",
    ".docx": "document",
    ".odt": "document",
    ".epub": "ebook",
    ".mobi": "ebook",
    ".csv": "csv",
    ".tsv": "tsv",
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".ppt": "presentation",
    ".pptx": "presentation",
    ".mp3": "audio",
    ".m4a": "audio",
    ".wav": "audio",
    ".flac": "audio",
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".avi": "video",
    ".zip": "archive",
    ".7z": "archive",
    ".rar": "archive",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}

TOPIC_RULES = (
    ("100m offers", "offers"),
    ("offers", "offers"),
    ("100m leads", "lead_generation"),
    ("lead", "lead_generation"),
    ("pricing", "pricing"),
    ("price", "pricing"),
    ("closing", "sales_closing"),
    ("sales", "sales_closing"),
    ("retention", "retention"),
    ("lifetime value", "lifetime_value"),
    ("brand", "branding"),
    ("hook", "marketing_creative"),
    ("ad", "marketing_creative"),
    ("gym launch", "gym_launch"),
    ("acquisition", "business_acquisition"),
    ("playbook", "business_playbook"),
)


def is_within(candidate: Path, parent: Path) -> bool:
    """Return whether *candidate* is located at or under *parent*."""
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def source_type(path: Path) -> str:
    return EXTENSION_TYPES.get(path.suffix.casefold(), "unknown")


def infer_topic(relative_path: Path) -> str:
    haystack = relative_path.as_posix().casefold().replace("_", " ").replace("-", " ")
    for needle, topic in TOPIC_RULES:
        if needle in haystack:
            return topic
    return "unclassified"


def matching_suspect_terms(relative_path: Path, suspect_terms: Iterable[str]) -> list[str]:
    haystack = relative_path.as_posix().casefold()
    return sorted({term for term in suspect_terms if term.casefold() in haystack})


def sha256_file(path: Path) -> str:
    """Read raw bytes only to calculate SHA-256; no parsing or extraction occurs."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def stable_id(prefix: str, root: Path, relative_path: Path) -> str:
    token = f"source-intake:{root.as_posix()}:{relative_path.as_posix()}"
    return f"{prefix}-{uuid.uuid5(uuid.NAMESPACE_URL, token).hex[:16]}"


def iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def base_record(
    *,
    prefix: str,
    path: Path,
    root: Path,
    stat: os.stat_result,
    rights_status: str,
    use_scope: str,
    content_hash: str | None,
    notes: str,
) -> dict[str, object]:
    relative_path = path.relative_to(root)
    return {
        "source_id": stable_id(prefix, root, relative_path),
        "path": str(path),
        "relative_path": relative_path.as_posix(),
        "type": source_type(path),
        "rights_status": rights_status,
        "use_scope": use_scope,
        "hash": content_hash,
        "topic": infer_topic(relative_path),
        "notes": notes,
        "size_bytes": stat.st_size,
        "modified_at": iso_timestamp(stat.st_mtime),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a metadata-and-SHA-256-only source intake manifest."
    )
    parser.add_argument(
        "--approved-root",
        action="append",
        required=True,
        metavar="PATH",
        help="Explicitly approved source directory to inventory; repeat for each root.",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="PATH",
        help="New JSON manifest path. The parent must already exist and cannot be inside an approved root.",
    )
    parser.add_argument(
        "--suspect-term",
        action="append",
        default=[],
        metavar="TERM",
        help="Additional case-insensitive path term to quarantine; default terms are always retained.",
    )
    return parser.parse_args()


def resolve_roots(raw_roots: list[str], parser: argparse.ArgumentParser) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw_root in raw_roots:
        unresolved_root = Path(raw_root).expanduser()
        if unresolved_root.is_symlink():
            parser.error(f"--approved-root must not be a symbolic link: {unresolved_root}")
        root = unresolved_root.resolve()
        if not root.is_dir():
            parser.error(f"--approved-root is not an existing directory: {root}")
        if root not in seen:
            roots.append(root)
            seen.add(root)
    return roots


def resolve_output(raw_output: str, roots: list[Path], parser: argparse.ArgumentParser) -> Path:
    output = Path(raw_output).expanduser().resolve()
    if output.exists():
        parser.error(f"--out already exists; refusing to overwrite it: {output}")
    if not output.parent.is_dir():
        parser.error(f"--out parent directory does not exist: {output.parent}")
    if any(is_within(output, root) for root in roots):
        parser.error("--out must be outside every approved root to avoid writing into source material")
    return output


def sorted_files(root: Path, warnings: list[dict[str, str]], counters: Counter[str]) -> Iterable[Path]:
    def on_walk_error(error: OSError) -> None:
        warnings.append({"path": str(error.filename), "warning": str(error)})

    for directory, directory_names, file_names in os.walk(root, topdown=True, followlinks=False, onerror=on_walk_error):
        directory_path = Path(directory)
        retained_directories: list[str] = []
        for name in directory_names:
            child = directory_path / name
            if child.is_symlink():
                counters["skipped_symlinks"] += 1
            else:
                retained_directories.append(name)
        directory_names[:] = sorted(retained_directories, key=str.casefold)
        for name in sorted(file_names, key=str.casefold):
            path = directory_path / name
            if path.is_symlink():
                counters["skipped_symlinks"] += 1
                continue
            if path.is_file():
                yield path


def inventory(root: Path, suspect_terms: list[str], manifest: dict[str, object], counters: Counter[str]) -> dict[str, int]:
    root_counts: Counter[str] = Counter()
    warnings = manifest["scan_warnings"]
    assert isinstance(warnings, list)
    for path in sorted_files(root, warnings, counters):
        root_counts["files_scanned"] += 1
        counters["files_scanned"] += 1
        try:
            stat = path.stat()
        except OSError as error:
            warning = {"path": str(path), "warning": f"Could not stat file: {error}"}
            warnings.append(warning)
            continue

        relative_path = path.relative_to(root)
        matched_terms = matching_suspect_terms(relative_path, suspect_terms)
        if matched_terms:
            record = base_record(
                prefix="qsrc",
                path=path,
                root=root,
                stat=stat,
                rights_status="quarantined_suspect_path",
                use_scope="quarantined_no_agent_access",
                content_hash=None,
                notes=(
                    "Automatically quarantined from agent access. No source bytes were hashed, "
                    "copied, parsed, or extracted."
                ),
            )
            record["quarantine_reason"] = (
                "Path matched suspect term(s): " + ", ".join(matched_terms)
            )
            quarantined = manifest["quarantined_sources"]
            assert isinstance(quarantined, list)
            quarantined.append(record)
            root_counts["quarantined_sources"] += 1
            counters["quarantined_sources"] += 1
            continue

        try:
            content_hash = sha256_file(path)
        except OSError as error:
            record = base_record(
                prefix="unreadable",
                path=path,
                root=root,
                stat=stat,
                rights_status="pending_inventory_read_error",
                use_scope="not_eligible_read_error",
                content_hash=None,
                notes=(
                    "The source could not be hashed during inventory. It is not eligible for "
                    "agent access or extraction."
                ),
            )
            record["read_error"] = str(error)
            unreadable = manifest["unreadable_sources"]
            assert isinstance(unreadable, list)
            unreadable.append(record)
            root_counts["unreadable_sources"] += 1
            counters["unreadable_sources"] += 1
            continue

        record = base_record(
            prefix="src",
            path=path,
            root=root,
            stat=stat,
            rights_status="pending_rights_review",
            use_scope="inventory_only",
            content_hash=content_hash,
            notes=(
                "Inventory-only record. No source content was copied, parsed, transcribed, "
                "extracted, or indexed. Separate rights and quality review is required before "
                "any agent access."
            ),
        )
        sources = manifest["sources"]
        assert isinstance(sources, list)
        sources.append(record)
        root_counts["inventory_only_sources"] += 1
        counters["inventory_only_sources"] += 1
    return {
        "files_scanned": root_counts["files_scanned"],
        "inventory_only_sources": root_counts["inventory_only_sources"],
        "quarantined_sources": root_counts["quarantined_sources"],
        "unreadable_sources": root_counts["unreadable_sources"],
    }


def main() -> int:
    args = parse_args()
    parser = argparse.ArgumentParser(add_help=False)
    roots = resolve_roots(args.approved_root, parser)
    output = resolve_output(args.out, roots, parser)
    suspect_terms = sorted(
        {term.casefold().strip() for term in (*DEFAULT_SUSPECT_TERMS, *args.suspect_term) if term.strip()}
    )

    manifest: dict[str, object] = {
        "$schema": "source-manifest.schema.json",
        "manifest_version": MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "inventory_mode": "metadata_and_sha256_only",
        "approved_roots": [],
        "sources": [],
        "quarantined_sources": [],
        "unreadable_sources": [],
        "scan_warnings": [],
        "summary": {},
    }
    counters: Counter[str] = Counter()
    for root in roots:
        counts = inventory(root, suspect_terms, manifest, counters)
        approved_roots = manifest["approved_roots"]
        assert isinstance(approved_roots, list)
        approved_roots.append(
            {
                "root_id": stable_id("root", root, Path(".")),
                "path": str(root),
                **counts,
            }
        )

    manifest["summary"] = {
        "files_scanned": counters["files_scanned"],
        "inventory_only_sources": counters["inventory_only_sources"],
        "quarantined_sources": counters["quarantined_sources"],
        "unreadable_sources": counters["unreadable_sources"],
        "skipped_symlinks": counters["skipped_symlinks"],
    }
    try:
        with output.open("x", encoding="utf-8", newline="\n") as destination:
            json.dump(manifest, destination, indent=2, ensure_ascii=False)
            destination.write("\n")
    except OSError as error:
        print(f"ERROR: could not write manifest: {error}", file=sys.stderr)
        return 1

    summary = manifest["summary"]
    assert isinstance(summary, dict)
    print(f"Inventory manifest written: {output}")
    print(
        "Scanned {files_scanned} file(s): {inventory_only_sources} inventory-only, "
        "{quarantined_sources} quarantined, {unreadable_sources} unreadable, "
        "{skipped_symlinks} symlink(s) skipped.".format(**summary)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
