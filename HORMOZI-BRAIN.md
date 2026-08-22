# Alex Hormozi Brain — Project Setup

This workspace is an ExpertPack foundation for an internal, source-grounded
decision-support agent. It is not an impersonation system and currently
contains no expert-source content.

## Components

- `packs/alex-hormozi-public/` — public-source pack; it fails closed until
  verified atoms are added.
- `runtime/ep-mcp/` — pinned fork of the MCP retrieval runtime.
- `config/ep-mcp.dev.yaml` — local-only server configuration.
- `private-input/` — ignored staging directory for user-provided books, notes,
  and other private material. Do not commit its contents.

## Verify the framework

```powershell
.\.venv\Scripts\expertpack.exe validate packs\alex-hormozi-public --strict
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\ep-mcp.exe validate --pack packs\alex-hormozi-public
```

## Start the local MCP server

Set a Gemini embedding key in the current shell, then run:

```powershell
$env:GEMINI_API_KEY = "..."
.\scripts\start-ep-mcp.ps1
```

The server binds only to `127.0.0.1` and keeps its generated index under
`runtime/ep-mcp-index/`, which is ignored by Git.

## Next step

Place any private source files in `private-input/` and tell me where they are.
We will inventory rights and source quality before extracting atoms or adding
them to a private ExpertPack.
