#!/usr/bin/env python3
"""Validate Paperclip skill packages and run representative workflow probes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_CASES = {
    "offers": ("hormozi-100m-offers", ("offer", "value equation", "grand slam")),
    "leads": ("hormozi-100m-leads", ("lead", "core four", "lead magnet")),
    "pricing": ("hormozi-pricing", ("pricing", "price", "value")),
    "sales": ("hormozi-closing", ("closing", "sales", "objection")),
    "retention": ("hormozi-retention", ("retention", "customers", "churn")),
    "scaling": ("hormozi-scaling-roadmap", ("scaling", "stage", "constraint")),
    "money_models": ("hormozi-money-models", ("money models", "upsell", "continuity")),
}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) != 3:
        return {}, text
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        data = {}
    return data if isinstance(data, dict) else {}, parts[2]


def validate_package(package: Path) -> dict[str, object]:
    skill_path = package / "SKILL.md"
    errors: list[str] = []
    if not skill_path.is_file():
        return {"package": package.name, "status": "fail", "errors": ["missing SKILL.md"]}
    raw = skill_path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = parse_frontmatter(raw)
    if frontmatter.get("name") != package.name:
        errors.append("frontmatter name does not match package directory")
    if not str(frontmatter.get("description", "")).strip():
        errors.append("missing description")
    for heading in ("How to Use This Skill", "Supporting Files", "Scope & Limits"):
        if f"## {heading}" not in body:
            errors.append(f"missing section: {heading}")
    if len(re.findall(r"^#{1,3}\s+", body, re.M)) < 3:
        errors.append("too few instructional headings")
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", body)
    missing_links = []
    for link in links:
        target = link.split("#", 1)[0].strip()
        if target and not target.endswith("/") and not (package / target).exists():
            missing_links.append(target)
    if missing_links:
        errors.append(f"missing supporting references: {missing_links[:5]}")
    supporting_files = [path for path in package.rglob("*") if path.is_file() and path.name != "SKILL.md"]
    if not supporting_files:
        errors.append("no supporting package files")
    return {"package": package.name, "status": "pass" if not errors else "fail", "errors": errors, "supporting_files": len(supporting_files), "instructional_headings": len(re.findall(r"^#{1,3}\s+", body, re.M))}


def run_workflow_probe(root: Path, domain: str, package_name: str, terms: tuple[str, ...]) -> dict[str, object]:
    path = root / package_name / "SKILL.md"
    text = path.read_text(encoding="utf-8", errors="replace").casefold() if path.is_file() else ""
    matched = [term for term in terms if term.casefold() in text]
    output_contract = {"retrieved_skill": package_name, "framework_terms": matched, "source_bound": bool(path.is_file()), "next_step": "apply only after retrieving cited source atoms", "boundary": "evidence-based simulation, not a statement or endorsement by Alex Hormozi"}
    return {"domain": domain, "package": package_name, "status": "pass" if len(matched) == len(terms) else "fail", "matched_terms": matched, "required_terms": list(terms), "output_contract": output_contract}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "private-input/skills/alex-hormozi")
    parser.add_argument("--output", type=Path, default=ROOT / "private-input/packs/alex-hormozi-brain-v1/meta/skill-validation.json")
    args = parser.parse_args()
    packages = sorted(path for path in args.root.iterdir() if path.is_dir()) if args.root.is_dir() else []
    package_results = [validate_package(path) for path in packages]
    workflow_results = [run_workflow_probe(args.root, domain, package, terms) for domain, (package, terms) in WORKFLOW_CASES.items()]
    report = {
        "report_version": "1.0",
        "package_count": len(packages),
        "package_failures": [row for row in package_results if row["status"] != "pass"],
        "workflow_count": len(workflow_results),
        "workflow_failures": [row for row in workflow_results if row["status"] != "pass"],
        "overall_status": "pass" if len(packages) == 24 and not [row for row in package_results if row["status"] != "pass"] and not [row for row in workflow_results if row["status"] != "pass"] else "fail",
        "packages": package_results,
        "representative_workflows": workflow_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["output"] = str(args.output)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("overall_status", "package_count", "workflow_count", "package_failures", "workflow_failures", "output")}, indent=2))
    return 0 if report["overall_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
