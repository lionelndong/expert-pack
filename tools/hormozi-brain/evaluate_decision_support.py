#!/usr/bin/env python3
"""Evaluate agent responses against the source-grounded decision contract.

Responses are supplied as JSONL records with ``id``, ``disposition``, and
``response`` fields. The evaluator never invents a response: without a real
response set it emits an explicit pending report. This keeps local acceptance
honest while providing a repeatable gate for a company agent or MCP client.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC = ROOT / "config/decision-support-evaluations.yaml"
DEFAULT_OUTPUT = ROOT / "private-input/packs/alex-hormozi-brain-v1/meta/decision-support-evaluation.json"


def load_spec(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise ValueError("decision-support spec must contain a cases list")
    return [case for case in cases if isinstance(case, dict)]


def load_responses(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    responses: dict[str, dict] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL at line {line_number}") from error
            if not isinstance(record, dict) or not record.get("id"):
                raise ValueError(f"response line {line_number} must contain an id")
            responses[str(record["id"])] = record
    return responses


def _contains(response: str, needle: object) -> bool:
    return str(needle).casefold() in response.casefold()


def _failure_triggered(response: str, failure: str) -> bool:
    lowered = response.casefold()
    if failure == "citation_lacks_source_id":
        return not bool(re.search(r"(?:approved|pending|restricted)-[a-z0-9][\w-]*", lowered))
    if failure == "citation_lacks_locator":
        return not bool(re.search(r"(?:p\.\s*\d+|timestamp\s+\d{2}:\d{2}:\d{2}|paragraph\s+\w+)", lowered))
    return False


def evaluate_case(case: dict, record: dict | None) -> dict:
    expected = case.get("expect") or {}
    if record is None:
        return {"id": case.get("id"), "status": "missing_response", "failures": ["response_missing"]}
    response = str(record.get("response", ""))
    failures: list[str] = []
    expected_disposition = expected.get("disposition")
    if expected_disposition and record.get("disposition") != expected_disposition:
        failures.append(f"disposition_expected:{expected_disposition}")
    for needle in expected.get("must_include", []) or []:
        if not _contains(response, needle):
            failures.append(f"missing:{needle}")
    for needle in expected.get("must_not_include", []) or []:
        if _contains(response, needle):
            failures.append(f"forbidden:{needle}")
    for failure in expected.get("failure_if", []) or []:
        if _failure_triggered(response, str(failure)):
            failures.append(str(failure))
    return {
        "id": case.get("id"),
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "disposition": record.get("disposition"),
    }


def evaluate(spec_path: Path, responses_path: Path | None, output: Path, require_responses: bool = False) -> dict:
    cases = load_spec(spec_path)
    responses = load_responses(responses_path)
    if not responses:
        status = "pending_agent_responses"
        results: list[dict] = []
    else:
        results = [evaluate_case(case, responses.get(str(case.get("id")))) for case in cases]
        status = "pass" if all(result["status"] == "pass" for result in results) else "fail"
    report = {
        "report_version": "1.0",
        "spec": str(spec_path.resolve()),
        "expected_cases": len(cases),
        "response_records": len(responses),
        "evaluated_cases": len(results),
        "overall_status": status,
        "live_agent_response_evaluation": status != "pending_agent_responses",
        "output": str(output.resolve()),
        "limitations": [
            "This checks response-contract markers and citations; it does not judge business correctness beyond the declared case expectations.",
            "A real agent/MCP response set is required before this gate can pass.",
        ],
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if require_responses and not responses:
        raise SystemExit("No agent responses supplied; use --responses JSONL or omit --require-responses for a pending report")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-responses", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.spec, args.responses, args.output, args.require_responses)
    print(json.dumps({key: report[key] for key in ("overall_status", "expected_cases", "response_records", "evaluated_cases", "output") if key in report}, indent=2))
    return 0 if report["overall_status"] in {"pass", "pending_agent_responses"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
