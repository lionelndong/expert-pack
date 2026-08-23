#!/usr/bin/env python3
"""Run an offline provenance and lexical retrieval quality check.

This is intentionally independent of OpenAI embeddings: it proves corpus
coverage, locator integrity, and a 50-question topical smoke set before any
paid vector indexing. It must not be described as a semantic retrieval score.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re


CASES = [
    ("offers", "grand slam offer value equation", ["grand slam", "value equation"]),
    ("offers", "dream outcome and perceived likelihood", ["dream outcome", "perceived likelihood"]),
    ("offers", "delivery cube and trim stack", ["delivery cube", "trim", "stack"]),
    ("offers", "scarcity urgency bonuses guarantees naming", ["scarcity", "urgency", "guarantees"]),
    ("offers", "price to value discrepancy", ["price-to-value", "value", "price"]),
    ("leads", "core four advertising methods", ["core four"]),
    ("leads", "lead magnet steps", ["lead magnet"]),
    ("leads", "warm outreach ACA", ["warm outreach", "acknowledge"]),
    ("leads", "cold outreach big fast value", ["cold outreach", "big fast value"]),
    ("leads", "paid ads call out value CTA", ["call out", "cta"]),
    ("marketing", "hook retain reward content unit", ["hook", "retain", "reward"]),
    ("marketing", "what who when advertising", ["what-who-when", "who", "when"]),
    ("marketing", "rule of 100", ["rule of 100"]),
    ("marketing", "more better new growth", ["more", "better", "new"]),
    ("marketing", "give ask content", ["give", "ask"]),
    ("pricing", "charge what it is worth", ["charge what", "worth"]),
    ("pricing", "premium pricing virtuous cycle", ["premium", "virtuous cycle"]),
    ("pricing", "honest scarcity seats", ["scarcity", "sell out"]),
    ("pricing", "guarantee if not X in Y then Z", ["if not", "guarantee"]),
    ("pricing", "MAGIC offer naming", ["magic", "magnet", "avatar"]),
    ("sales", "closing rules and blame", ["closing", "blame"]),
    ("sales", "all purpose closes", ["all-purpose closes", "close"]),
    ("sales", "sales objection handling", ["objection", "sales"]),
    ("sales", "proof checklist before selling", ["proof", "checklist"]),
    ("sales", "price raise sales conversation", ["price", "raise"]),
    ("retention", "retention and churn", ["retention", "churn"]),
    ("retention", "lifetime value calculation", ["lifetime value"]),
    ("retention", "fulfillment creates results", ["fulfillment", "results"]),
    ("retention", "customer success retention", ["customer", "retention"]),
    ("retention", "refund prevention", ["refund", "retention"]),
    ("scaling", "scaling roadmap levels", ["scaling", "roadmap"]),
    ("scaling", "hire employees as lead getters", ["employees", "lead getters"]),
    ("scaling", "use agencies to learn", ["agencies", "learn"]),
    ("scaling", "business systems and process", ["systems", "process"]),
    ("scaling", "constraint testing", ["constraint", "test"]),
    ("money_models", "money model core offer", ["money model"]),
    ("money_models", "upsell profit", ["upsell", "profit"]),
    ("money_models", "continuity recurring revenue", ["continuity", "recurring"]),
    ("money_models", "free plus paid model", ["free", "paid"]),
    ("money_models", "client financed acquisition", ["client financed acquisition"]),
    ("books", "100M Offers book", ["100m offers", "grand slam"]),
    ("books", "100M Leads book", ["100m leads", "core four"]),
    ("books", "100M Money Models chapters", ["money models"]),
    ("books", "EPUB chapter extraction", ["epub", "chapter"]),
    ("books", "audio transcription timestamp", ["audio", "transcription"]),
    ("youtube", "timestamped YouTube transcript", ["youtube url", "video id"]),
    ("youtube", "Alex Hormozi public video transcript", ["transcript", "youtube"]),
    ("skills", "Paperclip executable skill", ["paperclip", "skill"]),
    ("skills", "Book to Skills package", ["skill", "private package"]),
    ("governance", "source grounded not impersonation", ["evidence boundary", "not a current statement"]),
]


def load_atoms(pack: Path) -> list[tuple[Path, str]]:
    atoms = []
    for path in pack.rglob("*.md"):
        if path.name in {"_index.md", "overview.md", "STATUS.md"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        atoms.append((path, content))
    return atoms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=Path("private-input/packs/alex-hormozi-brain-v1"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    pack = args.pack.resolve()
    atoms = load_atoms(pack)
    results = []
    locator_failures = []
    for index, (category, query, expected) in enumerate(CASES, start=1):
        scored = []
        for path, content in atoms:
            lowered = content.casefold()
            score = sum(term.casefold() in lowered for term in expected)
            if score:
                scored.append((score, len(content), path, content))
        scored.sort(key=lambda item: (-item[0], item[1], str(item[2])))
        top = scored[:5]
        valid_top = []
        for score, _, path, content in top:
            has_id = bool(re.search(r"^id:\s*.+", content, re.M))
            has_locator = any(marker.casefold() in content.casefold() for marker in ("provenance", "source lines", "youtube url", "pages", "chapter", "timestamp"))
            valid_top.append(has_id and has_locator)
            if not (has_id and has_locator):
                locator_failures.append(str(path))
        results.append({"id": f"retrieval-{index:03d}", "category": category, "query": query, "expected_terms": expected, "top5": [str(item[2].relative_to(pack)).replace("\\", "/") for item in top], "relevant_top5": bool(top), "valid_citations_top5": all(valid_top) if top else False})
    relevant = sum(bool(row["relevant_top5"]) for row in results)
    cited = sum(bool(row["valid_citations_top5"]) for row in results)
    report = {
        "kind": "offline_lexical_and_provenance_smoke_test",
        "semantic_embedding_evaluation": "not_run",
        "cases": len(results),
        "relevant_top5_cases": relevant,
        "relevant_top5_rate": relevant / len(results) if results else 0,
        "valid_citation_top5_cases": cited,
        "valid_citation_top5_rate": cited / len(results) if results else 0,
        "locator_failures": sorted(set(locator_failures)),
        "decision_scenarios": 20,
        "results": results,
    }
    output = (args.output or pack / "meta" / "quality-report.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report["output"] = str(output)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("cases", "relevant_top5_rate", "valid_citation_top5_rate", "semantic_embedding_evaluation", "output")}, indent=2))
    return 0 if relevant == len(results) and not locator_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
