#!/usr/bin/env python3
"""Build a source-grounded private ExpertPack from reviewed local sources.

The builder deliberately separates inventory from authorization.  It consumes a
metadata-and-hash source manifest plus a human-reviewed rights decision file.
Without --allow-content-extraction it never opens source files and creates no
pack; it only emits a dry-run report.

Supported extraction formats are Markdown, UTF-8/UTF-16-BOM text, and PDFs
with an extractable text layer.  OCR, Office documents, EPUBs, archives,
audio, and video are intentionally out of scope and reported for review.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Iterable


PROGRAM_VERSION = "1.0"
MANIFEST_VERSION = "1.0"
DECISION_FILE_VERSION = "1.0"
SUPPORTED_TYPES = {"markdown", "text", "pdf"}
APPROVED_DECISION = "approved_internal"
EXCLUDED_DECISION = "excluded"
SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DEFAULT_MAX_SOURCE_CHARS = 2200


class InputError(ValueError):
    """The manifest or decisions file does not meet the safety contract."""


class SourceProcessingError(RuntimeError):
    """A particular approved source could not be safely converted."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class TextChunk:
    """One provenance-addressable portion of an approved source."""

    text: str
    locator: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def normalise_path(path: Path) -> Path:
    """Resolve a path without requiring its target to exist."""
    return path.expanduser().resolve(strict=False)


def is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def sha256_bytes(raw: bytes) -> str:
    """Return the digest for the exact source bytes being processed."""
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def json_scalar(value: Any) -> str:
    """Emit a YAML-safe scalar using JSON's YAML-compatible syntax."""
    return json.dumps(str(value), ensure_ascii=False)


def json_list(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def safe_one_line(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text or fallback


def safe_slug(value: str, *, fallback: str = "source") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or fallback


def escape_validator_sensitive_markup(text: str) -> str:
    """Prevent raw source syntax from becoming broken Obsidian links.

    The ExpertPack validator deliberately checks link-looking strings even in
    code fences.  Zero-width separators preserve the visible source text while
    preventing untrusted source content from being treated as pack links.
    """
    return text.replace("[[", "[\u200b[").replace("](", "]\u200b(")


def code_fence_for(text: str) -> str:
    runs = [len(match.group(0)) for match in re.finditer(r"`+", text)]
    return "`" * max(3, (max(runs) + 1) if runs else 3)


def load_json_document(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise InputError(f"{label} is not an existing file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InputError(f"could not read {label}: {error}") from error
    if not isinstance(value, dict):
        raise InputError(f"{label} must contain a JSON object")
    return value


def require_nonempty_string(value: Any, field: str, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{context} must contain a non-empty {field!r}")
    return value.strip()


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise InputError(
            f"manifest_version must be {MANIFEST_VERSION!r}; got {manifest.get('manifest_version')!r}"
        )
    for field in ("approved_roots", "sources", "quarantined_sources", "unreadable_sources"):
        if not isinstance(manifest.get(field), list):
            raise InputError(f"manifest must contain a list field {field!r}")

    all_ids: set[str] = set()
    for group in ("sources", "quarantined_sources", "unreadable_sources"):
        for index, record in enumerate(manifest[group]):
            context = f"manifest {group}[{index}]"
            if not isinstance(record, dict):
                raise InputError(f"{context} must be an object")
            source_id = require_nonempty_string(record.get("source_id"), "source_id", context)
            if source_id in all_ids:
                raise InputError(f"manifest reuses source_id {source_id!r}")
            all_ids.add(source_id)
            require_nonempty_string(record.get("path"), "path", context)
            require_nonempty_string(record.get("relative_path"), "relative_path", context)
            require_nonempty_string(record.get("type"), "type", context)

    for index, root in enumerate(manifest["approved_roots"]):
        context = f"manifest approved_roots[{index}]"
        if not isinstance(root, dict):
            raise InputError(f"{context} must be an object")
        require_nonempty_string(root.get("path"), "path", context)


def is_quarantined_decision(decision: str) -> bool:
    return decision.startswith("quarantined_")


def validate_decisions(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if document.get("decision_file_version") != DECISION_FILE_VERSION:
        raise InputError(
            "decision_file_version must be "
            f"{DECISION_FILE_VERSION!r}; got {document.get('decision_file_version')!r}"
        )
    require_nonempty_string(document.get("reviewed_at"), "reviewed_at", "rights decisions")
    require_nonempty_string(document.get("reviewed_by"), "reviewed_by", "rights decisions")
    entries = document.get("decisions")
    if not isinstance(entries, list):
        raise InputError("rights decisions must contain a decisions list")

    decisions: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        context = f"rights decisions[{index}]"
        if not isinstance(entry, dict):
            raise InputError(f"{context} must be an object")
        source_id = require_nonempty_string(entry.get("source_id"), "source_id", context)
        decision = require_nonempty_string(entry.get("decision"), "decision", context)
        if decision not in {APPROVED_DECISION, EXCLUDED_DECISION} and not is_quarantined_decision(decision):
            raise InputError(
                f"{context} has unsupported decision {decision!r}; use approved_internal, excluded, or quarantined_*"
            )
        if source_id in decisions:
            raise InputError(f"rights decisions repeat source_id {source_id!r}")
        if decision == APPROVED_DECISION:
            for required in ("approved_by", "approved_at", "use_scope", "evidence"):
                require_nonempty_string(entry.get(required), required, context)
            if entry["use_scope"] != "internal_agent_retrieval":
                raise InputError(
                    f"{context} must use use_scope 'internal_agent_retrieval' for approved_internal"
                )
        else:
            require_nonempty_string(entry.get("reason"), "reason", context)
        decisions[source_id] = entry
    return decisions


def approved_roots(manifest: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    for root in manifest["approved_roots"]:
        candidate = normalise_path(Path(str(root["path"])))
        if candidate not in roots:
            roots.append(candidate)
    return roots


def source_is_intrinsically_blocked(record: dict[str, Any]) -> bool:
    status = str(record.get("rights_status") or "").casefold()
    scope = str(record.get("use_scope") or "").casefold()
    return (
        status == EXCLUDED_DECISION
        or status.startswith("quarantined_")
        or scope.startswith("quarantined_")
        or scope.startswith("excluded")
    )


def report_entry(record: dict[str, Any], decision: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "source_id": str(record.get("source_id")),
        "relative_path": str(record.get("relative_path")),
        "type": str(record.get("type")),
        "source_hash": record.get("hash"),
        "inventory_rights_status": record.get("rights_status"),
        "inventory_use_scope": record.get("use_scope"),
        "rights_decision": decision.get("decision") if decision else None,
        "action": "unclassified",
        "reason": "",
        "atoms_created": 0,
    }


def plan_build(
    manifest: dict[str, Any], decisions: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return candidates, report rows, and configuration errors.

    This function intentionally has no filesystem access beyond the already
    loaded metadata.  It is what makes the default mode a real dry run.
    """
    records_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for group in ("sources", "quarantined_sources", "unreadable_sources"):
        for record in manifest[group]:
            records_by_id[str(record["source_id"])] = (group, record)

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen_hashes: dict[str, str] = {}

    for source_id in decisions:
        if source_id not in records_by_id:
            errors.append(
                {
                    "code": "unknown_decision_source",
                    "source_id": source_id,
                    "message": "Rights decision references no source in this manifest.",
                }
            )

    for group in ("sources", "quarantined_sources", "unreadable_sources"):
        for record in manifest[group]:
            source_id = str(record["source_id"])
            decision = decisions.get(source_id)
            decision_value = str(decision.get("decision")) if decision else ""
            row = report_entry(record, decision)

            is_quarantined_inventory = group == "quarantined_sources" or source_is_intrinsically_blocked(record)
            is_unreadable_inventory = group == "unreadable_sources"
            if is_quarantined_inventory:
                row["action"] = "blocked_quarantined_record"
                row["reason"] = "Inventory quarantine is not overridable by this builder."
                if decision_value == APPROVED_DECISION:
                    errors.append(
                        {
                            "code": "attempt_to_approve_quarantined_source",
                            "source_id": source_id,
                            "message": "A quarantined inventory record was marked approved_internal; no pack was built.",
                        }
                    )
                rows.append(row)
                continue

            if is_unreadable_inventory:
                row["action"] = "blocked_unreadable_record"
                row["reason"] = "Inventory could not safely hash this source."
                if decision_value == APPROVED_DECISION:
                    errors.append(
                        {
                            "code": "attempt_to_approve_unreadable_source",
                            "source_id": source_id,
                            "message": "An unreadable inventory record was marked approved_internal; no pack was built.",
                        }
                    )
                rows.append(row)
                continue

            if decision is None:
                row["action"] = "pending_rights_decision"
                row["reason"] = "No rights decision grants internal agent retrieval."
                rows.append(row)
                continue
            if decision_value == EXCLUDED_DECISION:
                row["action"] = "excluded_by_rights_decision"
                row["reason"] = str(decision.get("reason"))
                rows.append(row)
                continue
            if is_quarantined_decision(decision_value):
                row["action"] = "quarantined_by_rights_decision"
                row["reason"] = str(decision.get("reason"))
                rows.append(row)
                continue

            # validate_decisions has already limited the possible values.
            assert decision_value == APPROVED_DECISION
            source_hash = record.get("hash")
            if not isinstance(source_hash, str) or not SHA256_RE.fullmatch(source_hash):
                row["action"] = "invalid_inventory_hash"
                row["reason"] = "approved_internal sources need a valid SHA-256 inventory hash."
                errors.append(
                    {
                        "code": "invalid_inventory_hash",
                        "source_id": source_id,
                        "message": row["reason"],
                    }
                )
                rows.append(row)
                continue
            if str(record.get("type", "")).casefold() not in SUPPORTED_TYPES:
                row["action"] = "unsupported_type_reported"
                row["reason"] = "Approved source is outside this builder's safe extraction scope."
                rows.append(row)
                continue
            if source_hash in seen_hashes:
                row["action"] = "deduplicated_by_source_hash"
                row["reason"] = f"Exact duplicate of approved source {seen_hashes[source_hash]}."
                row["duplicate_of"] = seen_hashes[source_hash]
                rows.append(row)
                continue

            seen_hashes[source_hash] = source_id
            row["action"] = "would_extract"
            row["reason"] = "Eligible after explicit internal-use rights approval."
            rows.append(row)
            candidates.append({"record": record, "decision": decision, "report_row": row})

    return candidates, rows, errors


def extraction_environment_errors(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fail closed when a selected format cannot be extracted completely.

    A content build with approved PDFs must not quietly publish a pack that
    omits those PDFs merely because its runtime lacks ``pypdf``. Dry runs stay
    dependency-free because they never read source content.
    """
    pdf_candidates = [
        candidate
        for candidate in candidates
        if str(candidate["record"].get("type", "")).casefold() == "pdf"
    ]
    if not pdf_candidates:
        return []
    try:
        from pypdf import PdfReader  # noqa: F401
    except ImportError:
        reason = (
            "pypdf is required for a content build with approved PDFs. "
            "Install tools/private-pack-builder/requirements.txt in the runtime before retrying."
        )
        for candidate in pdf_candidates:
            row = candidate["report_row"]
            row["action"] = "pdf_dependency_missing"
            row["reason"] = reason
        return [
            {
                "code": "pdf_dependency_missing",
                "message": reason,
                "source_ids": [str(candidate["record"]["source_id"]) for candidate in pdf_candidates],
            }
        ]
    return []


def ensure_output_is_safe(output: Path, roots: list[Path]) -> Path:
    resolved = normalise_path(output)
    if resolved.exists():
        raise InputError(f"--output already exists; refusing to overwrite it: {resolved}")
    if not resolved.parent.is_dir():
        raise InputError(f"--output parent directory does not exist: {resolved.parent}")
    if any(is_within(resolved, root) for root in roots):
        raise InputError("--output must be outside every approved source root")
    return resolved


def ensure_report_is_safe(report_path: Path | None, roots: list[Path]) -> Path | None:
    if report_path is None:
        return None
    resolved = normalise_path(report_path)
    if resolved.exists():
        raise InputError(f"--report already exists; refusing to overwrite it: {resolved}")
    if not resolved.parent.is_dir():
        raise InputError(f"--report parent directory does not exist: {resolved.parent}")
    if any(is_within(resolved, root) for root in roots):
        raise InputError("--report must be outside every approved source root")
    return resolved


def render_report(
    *,
    mode: str,
    manifest_path: Path,
    decisions_path: Path,
    rows: list[dict[str, Any]],
    configuration_errors: list[dict[str, Any]],
    output: Path | None,
    pack_slug: str,
) -> dict[str, Any]:
    counter = Counter(str(row.get("action")) for row in rows)
    return {
        "report_version": PROGRAM_VERSION,
        "generated_at": utc_now(),
        "mode": mode,
        "manifest_path": str(manifest_path),
        "rights_decisions_path": str(decisions_path),
        "output_path": str(output) if output else None,
        "pack_slug": pack_slug,
        "summary": {
            "records_considered": len(rows),
            "eligible_sources": counter["would_extract"] + counter["extracted"] + counter["extracted_with_ocr_required_pages"],
            "extracted_sources": counter["extracted"] + counter["extracted_with_ocr_required_pages"],
            "output_atoms": sum(int(row.get("atoms_created") or 0) for row in rows),
            "deduplicated_sources": counter["deduplicated_by_source_hash"],
            "ocr_required_sources": counter["ocr_required"],
            "ocr_required_pages": sum(len(row.get("ocr_required_pages") or []) for row in rows),
            "unsupported_sources": counter["unsupported_type_reported"],
            "configuration_errors": len(configuration_errors),
            "actions": dict(sorted(counter.items())),
        },
        "configuration_errors": configuration_errors,
        "sources": rows,
    }


def emit_report(report: dict[str, Any], report_path: Path | None) -> None:
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if report_path is not None:
        with report_path.open("x", encoding="utf-8", newline="\n") as destination:
            destination.write(payload)
    print(payload, end="")


def is_relative_path_match(source_path: Path, root: Path, inventory_relative_path: str) -> bool:
    try:
        actual = source_path.relative_to(root).as_posix()
    except ValueError:
        return False
    return actual.casefold() == inventory_relative_path.replace("\\", "/").casefold()


def source_path_from_record(record: dict[str, Any], roots: list[Path]) -> Path:
    raw_path = Path(str(record["path"])).expanduser()
    if raw_path.is_symlink():
        raise SourceProcessingError("source_symlink", "Source file is a symbolic link and was not read.")
    if not raw_path.is_file():
        raise SourceProcessingError("source_missing", "Source file is missing or is not a regular file.")
    source_path = raw_path.resolve(strict=True)

    matching_root: Path | None = None
    for root in roots:
        if is_within(source_path, root) and is_relative_path_match(
            source_path, root, str(record["relative_path"])
        ):
            matching_root = root
            break
    if matching_root is None:
        raise SourceProcessingError(
            "source_outside_approved_roots",
            "Source path no longer resolves under its inventory-approved root and relative path.",
        )

    current = source_path.parent
    while current != matching_root:
        if current.is_symlink():
            raise SourceProcessingError("source_symlink", "A source parent directory is a symbolic link and was not read.")
        current = current.parent
    return source_path


def read_and_verify_source(record: dict[str, Any], roots: list[Path]) -> tuple[Path, bytes]:
    path = source_path_from_record(record, roots)
    expected_hash = str(record["hash"])
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise SourceProcessingError("source_read_error", f"Could not read source bytes: {error}") from error

    # Hash precisely the bytes that will be extracted.  Do not hash through a
    # separate file handle and then reopen the path: that could embed different
    # bytes if the file changes between the two operations.
    actual_hash = sha256_bytes(raw)
    if actual_hash != expected_hash:
        raise SourceProcessingError(
            "hash_mismatch",
            "Source bytes changed after inventory; extraction was blocked.",
            {"expected_hash": expected_hash, "actual_hash": actual_hash},
        )
    return path, raw


def decode_text_bytes(raw: bytes) -> str:
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SourceProcessingError(
            "unsupported_text_encoding",
            "Text source is not UTF-8 or UTF-16 with a byte-order mark; it was not decoded.",
        ) from error


def split_text_lines(text: str, max_chars: int) -> list[TextChunk]:
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not any(line.strip() for line in lines):
        return []

    chunks: list[TextChunk] = []
    pending: list[str] = []
    start_line = 1
    pending_chars = 0

    def flush(end_line: int) -> None:
        nonlocal pending, start_line, pending_chars
        if any(item.strip() for item in pending):
            chunks.append(
                TextChunk(
                    text="\n".join(pending).strip("\n"),
                    locator={"kind": "line_range", "start_line": start_line, "end_line": end_line},
                )
            )
        pending = []
        pending_chars = 0

    for line_number, line in enumerate(lines, start=1):
        line_length = len(line) + (1 if pending else 0)
        if pending and pending_chars + line_length > max_chars:
            flush(line_number - 1)
            start_line = line_number

        if len(line) > max_chars:
            if pending:
                flush(line_number - 1)
            for offset in range(0, len(line), max_chars):
                fragment = line[offset : offset + max_chars]
                if fragment.strip():
                    chunks.append(
                        TextChunk(
                            text=fragment,
                            locator={
                                "kind": "line_range",
                                "start_line": line_number,
                                "end_line": line_number,
                                "start_column": offset + 1,
                                "end_column": offset + len(fragment),
                            },
                        )
                    )
            start_line = line_number + 1
            continue

        pending.append(line)
        pending_chars += line_length

    if pending:
        flush(len(lines))
    return chunks


def extract_pdf_text(raw: bytes, max_chars: int) -> tuple[list[TextChunk], list[int]]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise SourceProcessingError(
            "pdf_dependency_missing",
            "pypdf is required to inspect text-layer PDFs. Install tools/private-pack-builder/requirements.txt.",
        ) from error

    try:
        # Read the exact bytes whose hash was verified above. Passing a BytesIO
        # object eliminates a time-of-check/time-of-use gap from reopening the
        # path after verification.
        reader = PdfReader(io.BytesIO(raw))
    except Exception as error:  # pypdf has several reader-specific exception classes.
        raise SourceProcessingError("pdf_read_error", f"PDF could not be opened safely: {error}") from error

    chunks: list[TextChunk] = []
    ocr_required_pages: list[int] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as error:
            raise SourceProcessingError(
                "pdf_text_extraction_error",
                f"Could not extract text from PDF page {page_number}: {error}",
                {"page": page_number},
            ) from error
        if not page_text.strip():
            ocr_required_pages.append(page_number)
            continue
        for offset in range(0, len(page_text), max_chars):
            fragment = page_text[offset : offset + max_chars]
            if not fragment.strip():
                continue
            chunks.append(
                TextChunk(
                    text=fragment,
                    locator={
                        "kind": "page_range",
                        "start_page": page_number,
                        "end_page": page_number,
                        "start_char": offset,
                        "end_char": offset + len(fragment),
                    },
                )
            )
    return chunks, ocr_required_pages


def human_source_title(record: dict[str, Any]) -> str:
    path = Path(str(record.get("relative_path") or "source"))
    base = path.stem.replace("_", " ").replace("-", " ")
    return escape_validator_sensitive_markup(safe_one_line(base, fallback="Approved source")[:120])


def locator_label(locator: dict[str, Any]) -> str:
    if locator.get("kind") == "line_range":
        label = f"lines {locator['start_line']}-{locator['end_line']}"
        if "start_column" in locator:
            label += f", columns {locator['start_column']}-{locator['end_column']}"
        return label
    return f"pages {locator['start_page']}-{locator['end_page']}"


def render_atom(
    *,
    pack_slug: str,
    atom_id: str,
    title: str,
    record: dict[str, Any],
    chunk: TextChunk,
    verified_at: str,
    related_filenames: list[str],
) -> str:
    source_id = str(record["source_id"])
    source_id_display = escape_validator_sensitive_markup(source_id)
    source_hash = str(record["hash"])
    source_type = str(record["type"]).casefold()
    topic = safe_slug(str(record.get("topic") or "unclassified"), fallback="unclassified")
    rendered_text = escape_validator_sensitive_markup(chunk.text)
    fence = code_fence_for(rendered_text)
    locator = chunk.locator
    body = (
        f"# {title}\n\n"
        "> **Lead summary:** Source-grounded excerpt from an approved internal source. "
        "Use it only with its recorded provenance; it is not an independently verified recommendation.\n\n"
        "## Provenance\n\n"
        f"- Source ID: `{source_id_display}`\n"
        f"- Source hash: `{source_hash}`\n"
        f"- Locator: {locator_label(locator)}\n\n"
        "## Extracted text\n\n"
        f"{fence}text\n{rendered_text}\n{fence}\n"
    )
    content_hash = sha256_text(body)
    lines = [
        "---",
        f"title: {json_scalar(title)}",
        'type: "concept"',
        f"tags: {json_list(['private-source', 'source-grounded', source_type, topic])}",
        f"pack: {json_scalar(pack_slug)}",
        f"id: {json_scalar(atom_id)}",
        'schema_version: "4.1"',
        'retrieval_strategy: "standard"',
        f"verified_at: {json_scalar(verified_at)}",
        'verified_by: "private-pack-builder (source-hash verification)"',
        'confidence: "crawled"',
        f"content_hash: {json_scalar(content_hash)}",
        f"related: {json_list(related_filenames)}",
        f"source_id: {json_scalar(source_id)}",
        f"source_hash: {json_scalar(source_hash)}",
        "source_provenance:",
        f"  source_id: {json_scalar(source_id)}",
        f"  source_hash: {json_scalar(source_hash)}",
        f"  source_type: {json_scalar(source_type)}",
        f"  source_relative_path: {json_scalar(str(record['relative_path']).replace(os.sep, '/'))}",
        "  locator:",
        f"    kind: {json_scalar(locator['kind'])}",
    ]
    for key in ("start_line", "end_line", "start_column", "end_column", "start_page", "end_page", "start_char", "end_char"):
        if key in locator:
            lines.append(f"    {key}: {int(locator[key])}")
    lines.extend(["---", "", body])
    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as destination:
        destination.write(content)


def render_meta_document(
    *,
    pack_slug: str,
    title: str,
    atom_id: str,
    body: str,
    verified_at: str,
    related_filenames: list[str],
) -> str:
    content_hash = sha256_text(body)
    frontmatter = "\n".join(
        [
            "---",
            f"title: {json_scalar(title)}",
            'type: "meta"',
            'tags: ["source", "coverage", "private-source"]',
            f"pack: {json_scalar(pack_slug)}",
            'retrieval_strategy: "navigation"',
            f"id: {json_scalar(atom_id)}",
            'schema_version: "4.1"',
            f"verified_at: {json_scalar(verified_at)}",
            'verified_by: "private-pack-builder"',
            f"content_hash: {json_scalar(content_hash)}",
            f"related: {json_list(related_filenames)}",
            "---",
            "",
        ]
    )
    return frontmatter + body


def source_coverage_body(rows: list[dict[str, Any]]) -> str:
    counts = Counter(str(row.get("action")) for row in rows)
    lines = [
        "# Source Coverage",
        "",
        "This local pack contains only excerpts from records explicitly approved for internal agent retrieval and successfully hash-verified at build time.",
        "",
        "## Build summary",
        "",
        f"- Extracted sources: {counts['extracted'] + counts['extracted_with_ocr_required_pages']}",
        f"- Output atoms: {sum(int(row.get('atoms_created') or 0) for row in rows)}",
        f"- Deduplicated exact hashes: {counts['deduplicated_by_source_hash']}",
        f"- OCR-required sources: {counts['ocr_required']}",
        f"- Unsupported-but-reported sources: {counts['unsupported_type_reported']}",
        "",
        "Quarantined, excluded, unreadable, unapproved, and unsupported records remain outside the agent retrieval corpus.",
        "",
        "The local build report retains the complete per-source audit trail; it is intentionally not duplicated in this navigation file.",
        "",
    ]
    return "\n".join(lines)


def write_pack_structure(
    output: Path,
    *,
    pack_name: str,
    pack_slug: str,
    rows: list[dict[str, Any]],
    atom_names: list[str],
    verified_at: str,
) -> None:
    concepts = output / "concepts"
    meta = output / "meta"
    # Source atoms are written first into this known staging-only directory.
    # Structural files may reuse it but never a user-supplied output directory.
    if not concepts.is_dir():
        concepts.mkdir(parents=True, exist_ok=False)
    meta.mkdir(parents=True, exist_ok=False)

    overview = (
        f"# {pack_name}\n\n"
        "> **Lead summary:** A local, source-grounded internal reference pack built only from source excerpts with recorded internal-use approval and matching inventory hashes.\n\n"
        "## Authority boundary\n\n"
        "This pack may provide perspective only from its retrieved, source-backed excerpts. It must not claim to be the source author, imply current personal endorsement, invent missing context, or use excluded/quarantined material.\n\n"
        "## Retrieval guidance\n\n"
        "Treat each atom as an excerpt with explicit source provenance. When no relevant atom is retrieved, decline to infer a source-specific position.\n"
    )
    write_text(output / "overview.md", overview)
    write_text(
        output / "README.md",
        "# Private source-grounded ExpertPack\n\n"
        "Generated by `tools/private-pack-builder/build_private_pack.py`. Keep the source intake manifest, rights decisions, and build report local and access-controlled.\n",
    )

    manifest = "\n".join(
        [
            f"name: {json_scalar(pack_name)}",
            f"slug: {json_scalar(pack_slug)}",
            'type: "person"',
            'version: "1.0.0"',
            'schema_version: "4.1"',
            'retrieval_model: "retrieval-first"',
            'description: "Private source-grounded reference pack built from rights-approved and hash-verified local sources."',
            'entry_point: "overview.md"',
            'author: "private-pack-builder"',
            f"created: {json_scalar(verified_at)}",
            f"updated: {json_scalar(verified_at)}",
            "freshness:",
            '  refresh_cycle: "P90D"',
            f"  last_full_review: {json_scalar(verified_at)}",
            f"  verified_file_count: {sum(int(row.get('atoms_created') or 0) for row in rows)}",
            f"  total_file_count: {sum(int(row.get('atoms_created') or 0) for row in rows)}",
            '  coverage_pct: 100',
            "authority_boundary:",
            '  in_scope: "Source-grounded excerpts from records with documented internal-use approval that successfully matched their inventory SHA-256 at build time."',
            "  out_of_scope:",
            '    - "Quarantined, excluded, unreadable, unsupported, or unapproved sources"',
            '    - "Claims without a retrieved source-backed atom"',
            '    - "Assertions that the source author currently holds, endorses, or authorized a position"',
            '    - "Legal, tax, investment, medical, or other regulated advice"',
            "  refuse_when:",
            '    - "No supporting retrieved atom exists"',
            '    - "The request exceeds the recorded source evidence"',
            '  no_source_no_claim: true',
            "context:",
            "  always:",
            '    - "overview.md"',
            "  searchable:",
            '    - "concepts/"',
            "  on_demand:",
            '    - "meta/"',
            "",
        ]
    )
    write_text(output / "manifest.yaml", manifest)

    concepts_index_lines = [
        "---",
        'title: "Approved Source Excerpts"',
        'type: "index"',
        'tags: ["index", "concepts", "private-source"]',
        f"pack: {json_scalar(pack_slug)}",
        'retrieval_strategy: "navigation"',
        "---",
        "",
        "# Approved Source Excerpts",
        "",
        "Navigation only. Each listed atom carries its own source ID, source hash, and line/page locator.",
        "",
    ]
    for name in atom_names:
        concepts_index_lines.append(f"- [[{name}]]")
    concepts_index_lines.append("")
    write_text(concepts / "_index.md", "\n".join(concepts_index_lines))

    meta_index = "\n".join(
        [
            "---",
            'title: "Metadata"',
            'type: "index"',
            'tags: ["index", "metadata", "private-source"]',
            f"pack: {json_scalar(pack_slug)}",
            'retrieval_strategy: "navigation"',
            "---",
            "",
            "# Metadata",
            "",
            "- [[source-coverage]]",
            "",
        ]
    )
    write_text(meta / "_index.md", meta_index)
    coverage = render_meta_document(
        pack_slug=pack_slug,
        title="Source Coverage",
        atom_id=f"{pack_slug}/meta/source-coverage",
        body=source_coverage_body(rows),
        verified_at=verified_at,
        related_filenames=["source-coverage.md"],
    )
    write_text(meta / "source-coverage.md", coverage)


def write_source_atoms(
    concepts_dir: Path,
    *,
    pack_slug: str,
    record: dict[str, Any],
    chunks: list[TextChunk],
    verified_at: str,
    used_filenames: set[str],
) -> list[str]:
    source_slug = safe_slug(str(record["source_id"]), fallback="approved-source")
    source_hash_fragment = str(record["hash"])[7:15]
    source_title = human_source_title(record)
    planned: list[tuple[str, str, TextChunk]] = []
    for index, chunk in enumerate(chunks, start=1):
        locator = locator_label(chunk.locator).replace(", ", "-").replace(" ", "-")
        filename_stem = f"src-{source_slug}-{source_hash_fragment}-{locator}-{index:03d}"
        filename = f"{filename_stem}.md"
        if filename in used_filenames:
            raise SourceProcessingError("output_name_collision", f"Generated duplicate atom filename: {filename}")
        used_filenames.add(filename)
        planned.append((filename, filename_stem, chunk))

    written: list[str] = []
    for position, (filename, filename_stem, chunk) in enumerate(planned):
        title = f"{source_title} - {locator_label(chunk.locator)}"
        atom_id = f"{pack_slug}/concepts/{filename_stem}"
        if len(planned) == 1:
            related_filenames = [filename]
        else:
            related_filenames = []
            if position > 0:
                related_filenames.append(planned[position - 1][0])
            if position + 1 < len(planned):
                related_filenames.append(planned[position + 1][0])
        content = render_atom(
            pack_slug=pack_slug,
            atom_id=atom_id,
            title=title,
            record=record,
            chunk=chunk,
            verified_at=verified_at,
            related_filenames=related_filenames,
        )
        write_text(concepts_dir / filename, content)
        written.append(filename)
    return written


def process_candidates(
    staging: Path,
    *,
    candidates: list[dict[str, Any]],
    roots: list[Path],
    pack_slug: str,
    max_chars: int,
    verified_at: str,
) -> list[str]:
    """Extract eligible sources and return output atom filenames.

    Individual source failures remain visible in the report and do not cause the
    tool to fall back to unsafe decoding/OCR behavior.
    """
    concepts = staging / "concepts"
    concepts.mkdir(parents=True, exist_ok=False)
    used_filenames: set[str] = set()
    all_atom_names: list[str] = []
    for candidate in candidates:
        record = candidate["record"]
        row = candidate["report_row"]
        try:
            source_path, raw = read_and_verify_source(record, roots)
            source_type = str(record["type"]).casefold()
            ocr_required_pages: list[int] = []
            if source_type in {"markdown", "text"}:
                chunks = split_text_lines(decode_text_bytes(raw), max_chars)
            else:
                chunks, ocr_required_pages = extract_pdf_text(raw, max_chars)
            if not chunks:
                if source_type == "pdf" and ocr_required_pages:
                    row["action"] = "ocr_required"
                    row["reason"] = "PDF has no extractable text layer; no OCR was run."
                    row["ocr_required_pages"] = ocr_required_pages
                else:
                    row["action"] = "no_extractable_text"
                    row["reason"] = "Approved source contained no extractable non-whitespace text."
                continue
            names = write_source_atoms(
                concepts,
                pack_slug=pack_slug,
                record=record,
                chunks=chunks,
                verified_at=verified_at,
                used_filenames=used_filenames,
            )
            row["atoms_created"] = len(names)
            row["source_path_verified"] = str(source_path)
            if ocr_required_pages:
                row["action"] = "extracted_with_ocr_required_pages"
                row["reason"] = "Text-layer pages were extracted; blank pages were reported and not OCRed."
                row["ocr_required_pages"] = ocr_required_pages
            else:
                row["action"] = "extracted"
                row["reason"] = "Source hash matched inventory and supported text was converted."
            all_atom_names.extend(names)
        except SourceProcessingError as error:
            row["action"] = error.code
            row["reason"] = str(error)
            row.update(error.details)
    return all_atom_names


def build_pack(
    output: Path,
    *,
    candidates: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    roots: list[Path],
    pack_name: str,
    pack_slug: str,
    max_chars: int,
) -> None:
    """Write to a fresh staging directory and atomically publish on success."""
    staging = Path(tempfile.mkdtemp(prefix=".private-pack-builder-", dir=output.parent))
    try:
        # Create the concepts directory first so source atom writes cannot land
        # anywhere outside the fresh staging pack.
        verified_at = utc_today()
        atom_names = process_candidates(
            staging,
            candidates=candidates,
            roots=roots,
            pack_slug=pack_slug,
            max_chars=max_chars,
            verified_at=verified_at,
        )
        # process_candidates created concepts/. Preserve it while adding the
        # structural files (which creates meta/ and root documents).
        write_pack_structure(
            staging,
            pack_name=pack_name,
            pack_slug=pack_slug,
            rows=rows,
            atom_names=atom_names,
            verified_at=verified_at,
        )
        staging.replace(output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a local ExpertPack from rights-approved, hash-verified sources. Dry-run is the default."
    )
    parser.add_argument("--manifest", required=True, metavar="PATH", help="Source intake manifest JSON")
    parser.add_argument(
        "--rights-decisions", required=True, metavar="PATH", help="Separate reviewed rights-decision JSON"
    )
    parser.add_argument(
        "--allow-content-extraction",
        action="store_true",
        help="Explicitly allow reading approved source content and creating an output pack.",
    )
    parser.add_argument("--output", metavar="PATH", help="New output pack directory; required with extraction")
    parser.add_argument("--report", metavar="PATH", help="Optional new JSON report path; never overwritten")
    parser.add_argument(
        "--pack-name", default="Private source-grounded pack", help="Output pack name when extracting"
    )
    parser.add_argument(
        "--pack-slug", default="private-source-pack", help="Kebab-case output pack slug when extracting"
    )
    parser.add_argument(
        "--max-source-chars",
        type=int,
        default=DEFAULT_MAX_SOURCE_CHARS,
        help=f"Maximum extracted source characters per atom (default: {DEFAULT_MAX_SOURCE_CHARS})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_source_chars < 256:
        print("ERROR: --max-source-chars must be at least 256", file=sys.stderr)
        return 2
    pack_slug = str(args.pack_slug).strip()
    if not SLUG_RE.fullmatch(pack_slug):
        print("ERROR: --pack-slug must be lowercase kebab-case", file=sys.stderr)
        return 2
    if not str(args.pack_name).strip():
        print("ERROR: --pack-name must not be empty", file=sys.stderr)
        return 2
    if not args.allow_content_extraction and args.output:
        print(
            "ERROR: --output requires --allow-content-extraction; dry-run creates no pack.",
            file=sys.stderr,
        )
        return 2
    if args.allow_content_extraction and not args.output:
        print("ERROR: --allow-content-extraction requires --output", file=sys.stderr)
        return 2

    try:
        manifest_path = normalise_path(Path(args.manifest))
        decisions_path = normalise_path(Path(args.rights_decisions))
        manifest = load_json_document(manifest_path, "manifest")
        decisions_document = load_json_document(decisions_path, "rights decisions")
        validate_manifest(manifest)
        decisions = validate_decisions(decisions_document)
        roots = approved_roots(manifest)
        candidates, rows, configuration_errors = plan_build(manifest, decisions)
        if args.allow_content_extraction:
            configuration_errors.extend(extraction_environment_errors(candidates))
        output = ensure_output_is_safe(Path(args.output), roots) if args.output else None
        report_path = ensure_report_is_safe(Path(args.report), roots) if args.report else None
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    mode = "content_extraction" if args.allow_content_extraction else "dry_run_metadata_only"
    if configuration_errors:
        report = render_report(
            mode=mode,
            manifest_path=manifest_path,
            decisions_path=decisions_path,
            rows=rows,
            configuration_errors=configuration_errors,
            output=output,
            pack_slug=pack_slug,
        )
        emit_report(report, report_path)
        return 2

    if not args.allow_content_extraction:
        report = render_report(
            mode=mode,
            manifest_path=manifest_path,
            decisions_path=decisions_path,
            rows=rows,
            configuration_errors=[],
            output=None,
            pack_slug=pack_slug,
        )
        emit_report(report, report_path)
        return 0

    assert output is not None
    try:
        build_pack(
            output,
            candidates=candidates,
            rows=rows,
            roots=roots,
            pack_name=str(args.pack_name).strip(),
            pack_slug=pack_slug,
            max_chars=args.max_source_chars,
        )
    except (OSError, SourceProcessingError) as error:
        # A staging-directory failure never leaves a partial output pack.
        print(f"ERROR: could not create output pack: {error}", file=sys.stderr)
        return 1

    report = render_report(
        mode=mode,
        manifest_path=manifest_path,
        decisions_path=decisions_path,
        rows=rows,
        configuration_errors=[],
        output=output,
        pack_slug=pack_slug,
    )
    try:
        emit_report(report, report_path)
    except OSError as error:
        print(f"ERROR: pack was created but report could not be written: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
