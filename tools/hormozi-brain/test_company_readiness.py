import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("company_readiness.py")
SPEC = importlib.util.spec_from_file_location("hormozi_company_readiness", MODULE_PATH)
assert SPEC and SPEC.loader
READINESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(READINESS)


def test_preflight_reports_external_prerequisites_without_secret_values(tmp_path, monkeypatch, capsys):
    config = tmp_path / "company.yaml"
    config.write_text(
        """
server:
  host: 0.0.0.0
  mcp_allowed_hosts: [hormozi-brain.internal]
  mcp_allowed_origins: [https://agents.internal]
  query_log_path: /var/log/ep-mcp/hormozi.jsonl
  rate_limit:
    enabled: true
    requests_per_minute: 120
    burst: 20
packs:
  - slug: alex-hormozi-brain
    path: private-input/packs/alex-hormozi-brain-v1
embedding:
  provider: openai
  model: text-embedding-3-small
""",
        encoding="utf-8",
    )
    output = tmp_path / "readiness.json"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EP_MCP_KEY_ALEX_HORMOZI_BRAIN", raising=False)
    monkeypatch.setattr(sys, "argv", ["company_readiness.py", "--config", str(config), "--output", str(output)])

    assert READINESS.main() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    stdout = capsys.readouterr().out
    assert report["overall_status"] == "pending_external_prerequisites"
    assert report["failures"] == []
    assert {"mcp_secret", "openai_secret", "company_gateway"} <= set(report["pending"])
    assert report["checks"]["audit_log"]["status"] == "pass"
    assert report["checks"]["no_inline_pack_keys"]["status"] == "pass"
    assert "OPENAI_API_KEY" not in stdout
    assert "EP_MCP_KEY_ALEX_HORMOZI_BRAIN" not in stdout
