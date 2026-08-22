#!/usr/bin/env python3
"""Generate a complete, profile-specific source rights decision file.

This tool is intentionally metadata-only.  It reads a source-intake manifest
and writes a *new* rights-decisions JSON document; it does not open, copy, or
extract any source file.  The two supported profiles keep the raw evidence
corpus separate from already-curated Paperclip skill artifacts.

Example:
    python tools/source-intake/generate_rights_decisions.py \
      --manifest C:\\safe\\hormozi-source-manifest.json \
      --profile evidence \
      --approved-by "designated-rights-holder" \
      --approved-at 2026-08-22 \
      --evidence "Company authorization for internal retrieval." \
      --out C:\\safe\\hormozi-evidence-decisions.json
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable


MANIFEST_VERSION = "1.0"
DECISION_FILE_VERSION = "1.0"
PAPERCLIP_ROOT_SUFFIX = ("paperclip.ai", "alex-hormozi-skills")
RAW_LIBRARY_ROOT_SUFFIX = ("private & shared", "alex hormozi knowledge library")
BOOK_LIBRARY_ROOT_SUFFIX = ("book-to-skill-master", "alex hormozi")

# These are navigation/catalog filenames, not source evidence.  The matching
# is deliberately exact after normalising spacing, punctuation, and case so a
# real book chapter such as "Library of Offers.md" is not silently excluded.
ORGANIZATIONAL_INDEX_MARKDOWN_STEMS = frozenset(
    {
        "readme",
        "index",
        "table of contents",
        "toc",
        "contents",
        "catalog",
        "library",
        "library index",
        "knowledge library",
        "alex hormozi knowledge library",
        "source index",
        "source list",
        "sources",
        "resource index",
        "resources",
        "manifest",
        # ExportBlock names collection-navigation files after their companion
        # folders, then adds a terminal hexadecimal identifier.
        "additional resources",
        "alex hormozi books collection",
        "alex hormozi study materials",
        "alex hormozi video transcripts",
        "complete playbooks library",
    }
)


class InputError(ValueError):
    """Raised when an input cannot safely support a complete decision file."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalise_path_text(value: object) -> tuple[str, ...]:
    """Return case-insensitive path components for Windows or POSIX input."""
    text = str(value).replace("\\", "/").strip()
    return tuple(part.casefold() for part in text.split("/") if part and part != ".")


def path_is_within(path: object, root: object) -> bool:
    path_parts = normalise_path_text(path)
    root_parts = normalise_path_text(root)
    return bool(root_parts) and path_parts[: len(root_parts)] == root_parts


def root_has_suffix(root: object, suffix: tuple[str, ...]) -> bool:
    parts = normalise_path_text(root)
    return parts[-len(suffix) :] == suffix


def path_has_directory(path: object, directory: str) -> bool:
    """Whether a relative or absolute path has *directory* as a directory."""
    return directory.casefold() in normalise_path_text(path)[:-1]


def markdown_filename(path: object) -> bool:
    return Path(str(path).replace("\\", "/")).suffix.casefold() in {".md", ".markdown"}


def normalise_filename_stem(path: object) -> str:
    stem = Path(str(path).replace("\\", "/")).stem.casefold()
    # Notion/ExportBlock folder indexes append a 32-character hexadecimal
    # identifier to an otherwise ordinary navigation filename.  Strip only
    # that terminal export marker before matching the known index names.
    stem = re.sub(r"[\s_-]+[0-9a-f]{32}$", "", stem)
    return re.sub(r"[\s_\-]+", " ", stem).strip()


def relative_path_is_direct_child(relative_path: object) -> bool:
    return len(normalise_path_text(relative_path)) == 1


def record_is_intrinsically_blocked(record: dict[str, Any]) -> bool:
    status = str(record.get("rights_status") or "").casefold()
    scope = str(record.get("use_scope") or "").casefold()
    return (
        status == "excluded"
        or status.startswith("quarantined_")
        or scope.startswith("quarantined_")
        or scope.startswith("excluded")
    )


def require_nonempty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{name} must be a non-empty string")
    return value.strip()


def parse_iso_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise InputError("approved_at must use ISO date format YYYY-MM-DD") from error
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a new, complete rights-decision file from a source inventory manifest."
    )
    parser.add_argument("--manifest", required=True, metavar="PATH", help="Existing source inventory manifest.")
    parser.add_argument(
        "--profile",
        required=True,
        choices=("evidence", "skills"),
        help="Pack separation profile to apply.",
    )
    parser.add_argument(
        "--approved-by",
        required=True,
        metavar="NAME",
        help="Rights holder or authorized reviewer recorded on every approval.",
    )
    parser.add_argument(
        "--approved-at",
        required=True,
        metavar="YYYY-MM-DD",
        help="Authorization date recorded on every approval.",
    )
    parser.add_argument(
        "--evidence",
        required=True,
        metavar="TEXT",
        help="Authorization evidence recorded on every approval.",
    )
    parser.add_argument(
        "--reviewed-by",
        metavar="NAME",
        help="Reviewer of this decision document; defaults to --approved-by.",
    )
    parser.add_argument(
        "--reviewed-at",
        metavar="ISO-8601",
        help="Review timestamp; defaults to the current UTC timestamp.",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="PATH",
        help="New output JSON path. Refuses to overwrite an existing file or write into a source root.",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InputError(f"manifest does not exist: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise InputError(f"could not read manifest {path}: {error}") from error
    if not isinstance(raw, dict):
        raise InputError("manifest must be a JSON object")
    if raw.get("manifest_version") != MANIFEST_VERSION:
        raise InputError(
            f"manifest_version must be {MANIFEST_VERSION!r}; got {raw.get('manifest_version')!r}"
        )
    for field in ("approved_roots", "sources", "quarantined_sources", "unreadable_sources"):
        if not isinstance(raw.get(field), list):
            raise InputError(f"manifest must contain a list field {field!r}")
    return raw


def approved_root_paths(manifest: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for index, root in enumerate(manifest["approved_roots"]):
        if not isinstance(root, dict):
            raise InputError(f"approved_roots[{index}] must be an object")
        path = require_nonempty_string(root.get("path"), f"approved_roots[{index}].path")
        if path not in paths:
            paths.append(path)
    return paths


def find_required_roots(manifest: dict[str, Any]) -> tuple[str, str, str]:
    roots = approved_root_paths(manifest)
    paperclip_roots = [root for root in roots if root_has_suffix(root, PAPERCLIP_ROOT_SUFFIX)]
    raw_roots = [root for root in roots if root_has_suffix(root, RAW_LIBRARY_ROOT_SUFFIX)]
    book_roots = [root for root in roots if root_has_suffix(root, BOOK_LIBRARY_ROOT_SUFFIX)]
    if len(paperclip_roots) != 1:
        raise InputError(
            "manifest must contain exactly one approved root ending in "
            "paperclip.ai/alex-hormozi-skills"
        )
    if len(raw_roots) != 1:
        raise InputError(
            "manifest must contain exactly one approved raw library root ending in "
            "Private & Shared/Alex Hormozi Knowledge Library"
        )
    if len(book_roots) != 1:
        raise InputError(
            "manifest must contain exactly one approved book library root ending in "
            "book-to-skill-master/ALEX HORMOZI"
        )
    return paperclip_roots[0], raw_roots[0], book_roots[0]


def require_record(record: object, group: str, index: int, source_ids: set[str]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise InputError(f"{group}[{index}] must be an object")
    source_id = require_nonempty_string(record.get("source_id"), f"{group}[{index}].source_id")
    if source_id in source_ids:
        raise InputError(f"manifest reuses source_id {source_id!r}")
    source_ids.add(source_id)
    require_nonempty_string(record.get("path"), f"{group}[{index}].path")
    require_nonempty_string(record.get("relative_path"), f"{group}[{index}].relative_path")
    return record


def blocked_decision(record: dict[str, Any], *, unreadable: bool = False) -> dict[str, str]:
    if unreadable:
        return {
            "source_id": str(record["source_id"]),
            "decision": "quarantined_unreadable_inventory_record",
            "reason": "Inventory could not safely hash this source; it is never eligible for agent retrieval.",
        }
    return {
        "source_id": str(record["source_id"]),
        "decision": "quarantined_inventory_record",
        "reason": "Inventory marks this source as quarantined or otherwise blocked; it is never eligible for agent retrieval.",
    }


def approval_decision(
    record: dict[str, Any], approved_by: str, approved_at: str, evidence: str, profile: str
) -> dict[str, str]:
    return {
        "source_id": str(record["source_id"]),
        "decision": "approved_internal",
        "approved_by": approved_by,
        "approved_at": approved_at,
        "use_scope": "internal_agent_retrieval",
        "evidence": evidence,
        "notes": (
            f"Included by the {profile} profile under the recorded authorization. This is internal source-grounded retrieval "
            "only and does not authorize public redistribution or attribution to Alex Hormozi."
        ),
    }


def excluded_decision(record: dict[str, Any], reason: str) -> dict[str, str]:
    return {"source_id": str(record["source_id"]), "decision": "excluded", "reason": reason}


def is_organizational_index_markdown(record: dict[str, Any], raw_root: str) -> bool:
    """Match only named navigation files directly at the raw library root."""
    return (
        path_is_within(record["path"], raw_root)
        and relative_path_is_direct_child(record["relative_path"])
        and markdown_filename(record["relative_path"])
        and normalise_filename_stem(record["relative_path"])
        in ORGANIZATIONAL_INDEX_MARKDOWN_STEMS
    )


def normal_record_decision(
    record: dict[str, Any],
    *,
    profile: str,
    paperclip_root: str,
    raw_root: str,
    book_root: str,
    approved_by: str,
    approved_at: str,
    evidence: str,
) -> dict[str, str]:
    """Apply a profile to a non-quarantined inventory record."""
    if record_is_intrinsically_blocked(record):
        return blocked_decision(record)

    in_paperclip = path_is_within(record["path"], paperclip_root)
    if profile == "evidence":
        if in_paperclip:
            return excluded_decision(
                record,
                "Excluded from the evidence pack: Paperclip skill artifact belongs only in the skills pack.",
            )
        if not (
            path_is_within(record["path"], raw_root)
            or path_is_within(record["path"], book_root)
        ):
            return excluded_decision(
                record,
                "Excluded from the evidence pack: source is outside the reviewed raw and book-library roots.",
            )
        if is_organizational_index_markdown(record, raw_root):
            return excluded_decision(
                record,
                "Excluded from the evidence pack: organizational index Markdown is navigation metadata, not source evidence.",
            )
        return approval_decision(record, approved_by, approved_at, evidence, profile)

    # skills profile: take only curated Paperclip Markdown that is not its raw
    # /source/ material.  Every other normal record is explicitly excluded,
    # including all raw and book-library material.
    if not in_paperclip:
        return excluded_decision(
            record,
            "Excluded from the skills pack: only curated Paperclip Markdown skills are selected; raw or book-library material belongs in the evidence pack.",
        )
    if path_has_directory(record["relative_path"], "source"):
        return excluded_decision(
            record,
            "Excluded from the skills pack: Paperclip source/ material is raw reference material and belongs in the evidence pack.",
        )
    if not markdown_filename(record["relative_path"]):
        return excluded_decision(
            record,
            "Excluded from the skills pack: only curated Paperclip Markdown skill files are selected.",
        )
    return approval_decision(record, approved_by, approved_at, evidence, profile)


def generate_decisions(
    manifest: dict[str, Any],
    *,
    profile: str,
    approved_by: str,
    approved_at: str,
    evidence: str,
    reviewed_by: str,
    reviewed_at: str,
) -> dict[str, Any]:
    """Return a complete rights-decisions document for one separation profile."""
    if profile not in {"evidence", "skills"}:
        raise InputError(f"unsupported profile {profile!r}")
    paperclip_root, raw_root, book_root = find_required_roots(manifest)
    decisions: list[dict[str, str]] = []
    source_ids: set[str] = set()

    for group in ("sources", "quarantined_sources", "unreadable_sources"):
        for index, raw_record in enumerate(manifest[group]):
            record = require_record(raw_record, group, index, source_ids)
            if group == "quarantined_sources":
                decisions.append(blocked_decision(record))
            elif group == "unreadable_sources":
                decisions.append(blocked_decision(record, unreadable=True))
            else:
                decisions.append(
                    normal_record_decision(
                        record,
                        profile=profile,
                        paperclip_root=paperclip_root,
                        raw_root=raw_root,
                        book_root=book_root,
                        approved_by=approved_by,
                        approved_at=approved_at,
                        evidence=evidence,
                    )
                )

    return {
        "$schema": "rights-decisions.schema.json",
        "decision_file_version": DECISION_FILE_VERSION,
        "reviewed_at": reviewed_at,
        "reviewed_by": reviewed_by,
        "decisions": decisions,
    }


def validate_generated_document(document: dict[str, Any]) -> None:
    """Validate invariants also enforced by the decision schema and builder."""
    expected_keys = {
        "$schema",
        "decision_file_version",
        "reviewed_at",
        "reviewed_by",
        "decisions",
    }
    if set(document) != expected_keys:
        raise InputError("generated decision document has unexpected top-level fields")
    if document["$schema"] != "rights-decisions.schema.json":
        raise InputError("generated document has an invalid schema reference")
    if document["decision_file_version"] != DECISION_FILE_VERSION:
        raise InputError("generated document has an invalid decision file version")
    require_nonempty_string(document["reviewed_at"], "reviewed_at")
    require_nonempty_string(document["reviewed_by"], "reviewed_by")

    seen: set[str] = set()
    for index, decision in enumerate(document["decisions"]):
        if not isinstance(decision, dict):
            raise InputError(f"generated decisions[{index}] is not an object")
        source_id = require_nonempty_string(decision.get("source_id"), f"generated decisions[{index}].source_id")
        if source_id in seen:
            raise InputError(f"generated decisions repeat source_id {source_id!r}")
        seen.add(source_id)
        value = require_nonempty_string(decision.get("decision"), f"generated decisions[{index}].decision")
        if value == "approved_internal":
            for field in ("approved_by", "approved_at", "use_scope", "evidence"):
                require_nonempty_string(decision.get(field), f"generated decisions[{index}].{field}")
            if decision["use_scope"] != "internal_agent_retrieval":
                raise InputError("generated approvals must use internal_agent_retrieval")
        elif value == "excluded" or value.startswith("quarantined_"):
            require_nonempty_string(decision.get("reason"), f"generated decisions[{index}].reason")
        else:
            raise InputError(f"generated decisions[{index}] has invalid decision {value!r}")


def ensure_safe_output(raw_path: str, manifest: dict[str, Any]) -> Path:
    output = Path(raw_path).expanduser().resolve()
    if output.exists():
        raise InputError(f"--out already exists; refusing to overwrite it: {output}")
    if not output.parent.is_dir():
        raise InputError(f"--out parent directory does not exist: {output.parent}")
    for root in approved_root_paths(manifest):
        if path_is_within(str(output), root):
            raise InputError("--out must be outside every approved source root")
    return output


def decision_counts(decisions: Iterable[dict[str, str]]) -> dict[str, int]:
    counts = {"approved_internal": 0, "excluded": 0, "quarantined": 0}
    for decision in decisions:
        value = decision["decision"]
        if value == "approved_internal":
            counts["approved_internal"] += 1
        elif value == "excluded":
            counts["excluded"] += 1
        else:
            counts["quarantined"] += 1
    return counts


def main() -> int:
    args = parse_args()
    try:
        approved_by = require_nonempty_string(args.approved_by, "approved_by")
        approved_at = parse_iso_date(require_nonempty_string(args.approved_at, "approved_at"))
        evidence = require_nonempty_string(args.evidence, "evidence")
        reviewed_by = require_nonempty_string(args.reviewed_by or approved_by, "reviewed_by")
        reviewed_at = require_nonempty_string(args.reviewed_at or utc_now(), "reviewed_at")
        manifest_path = Path(args.manifest).expanduser().resolve()
        manifest = load_manifest(manifest_path)
        document = generate_decisions(
            manifest,
            profile=args.profile,
            approved_by=approved_by,
            approved_at=approved_at,
            evidence=evidence,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )
        validate_generated_document(document)
        output = ensure_safe_output(args.out, manifest)
        with output.open("x", encoding="utf-8", newline="\n") as destination:
            json.dump(document, destination, indent=2, ensure_ascii=False)
            destination.write("\n")
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"ERROR: could not write decision file: {error}", file=sys.stderr)
        return 1

    counts = decision_counts(document["decisions"])
    print(f"Rights decisions written: {output}")
    print(
        "Profile {profile}: {approved_internal} approved, {excluded} excluded, {quarantined} quarantined.".format(
            profile=args.profile, **counts
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
