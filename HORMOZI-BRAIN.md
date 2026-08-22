# Alex Hormozi Brain — Project Setup

This workspace is an ExpertPack foundation for an internal, source-grounded
decision-support agent. It is not an impersonation system. Expert-source
content stays in ignored local directories; no private source content is
committed to this repository.

## Components

- `packs/alex-hormozi-public/` — public-source pack; it fails closed until
  verified atoms are added.
- `runtime/ep-mcp/` — pinned fork of the MCP retrieval runtime.
- `config/ep-mcp.dev.yaml` — tracked development configuration for the public
  scaffold.
- `config/ep-mcp.local.yaml` — ignored local configuration that wires the
  approved private packs into the MCP runtime.
- `private-input/` — ignored intake, reports, and generated private packs.
  Do not commit its contents.
- `tools/source-intake/` — metadata-only inventory and explicit rights-profile
  generation; flagged material is quarantined rather than extracted.
- `tools/private-pack-builder/` — opt-in local builder for approved material,
  with page/line provenance and OCR/unsupported-file reporting.
- `guides/agent-decision-support-contract.md` — required evidence-first rules
  for agents that consume the packs.

## Verify the framework

```powershell
.\.venv\Scripts\expertpack.exe validate packs\alex-hormozi-public --strict
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\ep-mcp.exe validate --pack packs\alex-hormozi-public
```

## Start the local MCP server

The local configuration loads the approved evidence and skills packs. Starting
the service needs an embedding provider. Gemini sends material to Google's
embedding API; use it only after approving that data flow. Azure OpenAI is
also supported when it is your approved provider. Keep provider credentials in
the shell or a secret manager, never in Git.

For an approved Gemini setup:

```powershell
$env:GEMINI_API_KEY = "..."
.\scripts\start-ep-mcp.ps1 -AllowRemoteEmbedding
```

The server binds only to `127.0.0.1` and keeps its generated index under
`runtime/ep-mcp-index/`, which is ignored by Git.

This is a local development setup, not a company-network deployment. Before
exposing it beyond the machine, put the MCP endpoints behind your company’s
authenticated gateway and explicitly configure the allowed origins. The
runtime’s optional API-key check covers its direct HTTP search route, not the
mounted MCP routes.

## Rebuild an approved private pack

Use the inventory and rights decision tools before building. The builder only
extracts records explicitly marked `approved_internal`; it never extracts
quarantined material. See `tools/source-intake/README.md` and
`tools/private-pack-builder/README.md` for the exact commands.
