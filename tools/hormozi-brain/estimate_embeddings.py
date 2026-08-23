#!/usr/bin/env python3
"""Estimate OpenAI embedding usage before a local index build."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=Path("private-input/packs/alex-hormozi-brain-v1"))
    parser.add_argument("--price-per-million", type=float, default=0.02)
    args = parser.parse_args()
    chars = 0
    files = 0
    for path in args.pack.rglob("*.md"):
        if path.name == "_index.md":
            continue
        chars += len(path.read_text(encoding="utf-8", errors="replace"))
        files += 1
    # Conservative planning estimate: four characters per token.
    estimated_tokens = (chars + 3) // 4
    report = {
        "pack": str(args.pack.resolve()),
        "files": files,
        "characters": chars,
        "estimated_input_tokens": estimated_tokens,
        "model": "text-embedding-3-small",
        "price_per_million_input_tokens_usd": args.price_per_million,
        "projected_embedding_cost_usd": round(estimated_tokens / 1_000_000 * args.price_per_million, 6),
        "requires_openai_api_key": True,
        "local_model": False,
    }
    output = args.pack / "meta" / "embedding-estimate.json"
    report["output"] = str(output)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("files", "estimated_input_tokens", "projected_embedding_cost_usd", "output")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
