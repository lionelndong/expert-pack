#!/usr/bin/env python3
"""Preflight the company MCP configuration without exposing secret values."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/ep-mcp.company.example.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "private-input/packs/alex-hormozi-brain-v1/meta/company-deployment-readiness.json")
    args = parser.parse_args()
    raw = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    server = raw.get("server", {})
    packs = raw.get("packs", [])
    embedding = raw.get("embedding", {})
    pack = next((item for item in packs if item.get("slug") == "alex-hormozi-brain"), {})
    pack_path = (ROOT / str(pack.get("path", ""))).resolve()
    audit_path = Path(str(server.get("query_log_path", ""))).expanduser().resolve()
    inline_pack_keys = any(bool(item.get("api_keys")) for item in packs if isinstance(item, dict))
    inline_embedding_keys = any(
        bool(embedding.get(field))
        for field in ("api_key", "azure_api_key")
    )
    try:
        audit_inside_pack = audit_path.is_relative_to(pack_path)
    except AttributeError:  # pragma: no cover - Python 3.8 compatibility.
        audit_inside_pack = str(audit_path).startswith(str(pack_path) + os.sep)
    checks = {
        "network_bind": {"status": "pass" if server.get("host") not in {"127.0.0.1", "localhost", "::1"} else "fail", "detail": "company config binds beyond loopback"},
        "allowed_hosts": {"status": "pass" if server.get("mcp_allowed_hosts") else "fail", "detail": "MCP host allowlist is configured"},
        "allowed_origins": {"status": "pass" if server.get("mcp_allowed_origins") else "fail", "detail": "MCP origin allowlist is configured"},
        "rate_limit": {"status": "pass" if server.get("rate_limit", {}).get("enabled") and server.get("rate_limit", {}).get("requests_per_minute", 0) > 0 and server.get("rate_limit", {}).get("burst", 0) > 0 else "fail", "detail": "process-local safety limit is enabled"},
        "audit_log": {"status": "pass" if server.get("query_log_path") and not audit_inside_pack else "fail", "detail": "JSONL audit path is configured outside the private pack"},
        "no_inline_pack_keys": {"status": "fail" if inline_pack_keys else "pass", "detail": "pack API keys are injected through the environment/secret manager"},
        "no_inline_embedding_keys": {"status": "fail" if inline_embedding_keys else "pass", "detail": "embedding credentials are injected through the environment/secret manager"},
        "private_pack": {"status": "pass" if pack_path.is_dir() else "fail", "detail": "generated private pack exists"},
        "private_pack_ignored": {"status": "pass" if subprocess.run(["git", "check-ignore", "-q", str(pack_path.relative_to(ROOT))], cwd=ROOT, check=False).returncode == 0 else "fail", "detail": "private pack is excluded from Git"},
        "openai_embedding_provider": {"status": "pass" if embedding.get("provider") == "openai" and embedding.get("model") == "text-embedding-3-small" else "fail", "detail": "direct OpenAI embedding provider is configured"},
        "mcp_secret": {"status": "pass" if os.environ.get("EP_MCP_KEY_ALEX_HORMOZI_BRAIN") else "pending_external_secret", "detail": "secret presence checked without printing its value"},
        "openai_secret": {"status": "pass" if os.environ.get("OPENAI_API_KEY") else "pending_external_secret", "detail": "API key presence checked without printing its value"},
        "company_gateway": {"status": "pending_external_gateway", "detail": "company gateway, TLS, identity policy, and distributed limits are outside this workspace"},
    }
    pending = [name for name, value in checks.items() if value["status"].startswith("pending")]
    failures = [name for name, value in checks.items() if value["status"] == "fail"]
    report = {"report_version": "1.0", "overall_status": "ready_for_gateway" if not pending and not failures else "pending_external_prerequisites", "config": str(args.config.resolve()), "checks": checks, "pending": pending, "failures": failures}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"overall_status": report["overall_status"], "pending": pending, "failures": failures, "output": str(args.output)}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
