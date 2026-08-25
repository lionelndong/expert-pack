#!/usr/bin/env python3
"""Exercise the four Hormozi MCP tools against the real generated pack.

This is deliberately provider-free: it loads the pack and invokes the MCP
contract with an in-memory retrieval stub, so it can validate citations,
coverage redaction, skill access, and source lookup before an OpenAI key is
approved.  It never prints source text or private filesystem paths.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from pathlib import Path
from unittest.mock import AsyncMock

from ep_mcp.pack.loader import load_pack
from ep_mcp.server import create_pack_mcp


def _payload(result) -> dict:
    if result.structured_content:
        return result.structured_content
    if not result.content or not getattr(result.content[0], "text", None):
        raise RuntimeError("MCP tool returned no structured or text payload")
    value = json.loads(result.content[0].text)
    if not isinstance(value, dict):
        raise RuntimeError("MCP tool returned a non-object payload")
    return value


def _youtube_source_id(pack) -> str:
    for path, pack_file in sorted(pack.files.items()):
        if not path.startswith("youtube/"):
            continue
        source_id = pack_file.provenance.id
        if source_id:
            return source_id
    raise RuntimeError("generated pack has no provenance-backed YouTube source")


def _assert_no_private_paths(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    if "source_files" in serialized or re.search(r"[A-Za-z]:\\", serialized):
        raise AssertionError("MCP payload exposed a private source path")


async def run(pack_path: Path, skill_name: str) -> dict[str, object]:
    from mcp import Client

    pack = load_pack(pack_path)
    engine = AsyncMock()
    engine.search.return_value = []
    mcp = create_pack_mcp(pack.slug, pack, engine)
    source_id = _youtube_source_id(pack)

    async with Client(mcp) as client:
        tool_names = {tool.name for tool in (await client.list_tools()).tools}
        required = {
            "search_hormozi_brain",
            "get_hormozi_source",
            "get_hormozi_skill",
            "get_brain_coverage",
        }
        missing = sorted(required - tool_names)
        if missing:
            raise AssertionError(f"missing Hormozi MCP tools: {missing}")

        coverage = _payload(await client.call_tool("get_brain_coverage", {}))
        if coverage.get("inventory_records") != 428:
            raise AssertionError(f"unexpected inventory count: {coverage.get('inventory_records')}")
        transcripts = coverage.get("transcripts", {})
        if transcripts.get("unique_videos") != 273:
            raise AssertionError(f"unexpected video count: {transcripts.get('unique_videos')}")
        if transcripts.get("duplicate_sections_removed") != 3:
            raise AssertionError("transcript duplicate-removal count is not preserved")
        _assert_no_private_paths(coverage)

        source = _payload(
            await client.call_tool(
                "get_hormozi_source",
                {"source_id": source_id},
            )
        )
        if source.get("source_id") != source_id:
            raise AssertionError("source lookup did not preserve its provenance ID")
        if not source.get("locator"):
            raise AssertionError("source lookup returned no citation locator")
        _assert_no_private_paths({key: value for key, value in source.items() if key != "content"})

        skill = _payload(await client.call_tool("get_hormozi_skill", {"skill_name": skill_name}))
        if skill.get("error") or skill.get("source_id") != f"{pack.slug}/agent-skills/{skill_name}":
            raise AssertionError(f"skill lookup failed for {skill_name}")
        if not skill.get("content") or not skill.get("files"):
            raise AssertionError("skill lookup returned no executable content or supporting files")
        _assert_no_private_paths({key: value for key, value in skill.items() if key != "content"})

        search = _payload(
            await client.call_tool(
                "search_hormozi_brain",
                {"query": "pricing", "max_results": 3},
            )
        )
        if not isinstance(search.get("results"), list):
            raise AssertionError("search tool did not return a results list")

    return {
        "status": "pass",
        "pack": pack.slug,
        "files": len(pack.files),
        "required_tools": sorted(required),
        "coverage": {
            "inventory_records": coverage.get("inventory_records"),
            "unique_videos": transcripts.get("unique_videos"),
            "pending_official_videos": len(
                coverage.get("coverage_categories", {})
                .get("missing", {})
                .get("official_captionless_videos", [])
            ),
        },
        "source_id_checked": source_id,
        "skill_checked": skill_name,
        "search_result_count": len(search.get("results", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pack",
        type=Path,
        default=Path("private-input/packs/alex-hormozi-brain-v1"),
    )
    parser.add_argument("--skill", default="hormozi-pricing")
    args = parser.parse_args()
    # Runtime debug logs include pack-relative paths and query text.  The
    # contract report is intentionally metadata-only, so keep those logs out
    # of the operator-facing output.
    logging.getLogger("ep_mcp").setLevel(logging.CRITICAL)
    report = asyncio.run(run(args.pack, args.skill))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
