# Alex Hormozi Brain — Project Setup

This workspace is an ExpertPack foundation for an internal, source-grounded
decision-support agent. It is not an impersonation system. Expert-source
content stays in ignored local directories; no private source content is
committed to this repository.

## Components

- `private-input/packs/alex-hormozi-brain-v1/` — generated local composite
  brain containing normalized public transcripts, approved evidence, EPUB
  chapters, and skill entrypoints. It is ignored by Git.
- `packs/alex-hormozi-public/` — public-source scaffold; it fails closed until
  verified public atoms are added.
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
- `tools/hormozi-brain/build_brain.py` — builds the composite private pack,
  normalizes all 273 supplied videos into timestamp-bounded atoms, extracts
  the EPUB, records audio metadata, and mirrors the 24 executable skills.
- `tools/hormozi-brain/fetch_official.py` — opt-in metadata/subtitle-only
  refresh for explicitly verified official channels.
- `tools/hormozi-brain/transcribe_official.py` — fail-closed audio-only
  transcription for captionless verified videos; media is temporary and never
  retained.
- `tools/hormozi-brain/evaluate_decision_support.py` — evaluates real agent
  response JSONL against the 20-case evidence/inference/conflict contract.
- `tools/hormozi-brain/ocr_sources.py` and `transcribe_audio.py` — fail-closed
  adapters for remaining OCR/audio gaps.
- `guides/agent-decision-support-contract.md` — required evidence-first rules
  for agents that consume the packs.
- `guides/hormozi-restricted-source-authorization.md` — authorization record
  required before either restricted pricing-playbook copy can be processed.

## Verify the framework

```powershell
.\.venv\Scripts\expertpack.exe validate packs\alex-hormozi-public --strict
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\ep-mcp.exe validate --pack packs\alex-hormozi-public
```

## Start the local MCP server

The local configuration loads the one composite Hormozi brain. Starting the
service needs an embedding provider. The configured default is direct OpenAI
`text-embedding-3-small`; it sends source text to the existing OpenAI API and
keeps vectors in local SQLite. Keep provider credentials in the shell or a
secret manager, never in Git.

For an approved OpenAI setup:

```powershell
$env:OPENAI_API_KEY = "..."
.\scripts\start-ep-mcp.ps1 -AllowRemoteEmbedding
```

The server binds only to `127.0.0.1` and keeps its generated index under
`runtime/ep-mcp-index/`, which is ignored by Git.

This is a local development setup, not a company-network deployment. Before
exposing it beyond the machine, put the MCP endpoints behind your company’s
authenticated gateway and explicitly configure the allowed origins. The
runtime applies the per-pack API-key policy to both its direct HTTP search
route and mounted MCP routes; the company gateway should still provide the
network-level controls, distributed rate limits, and audit logging. The
company example also enables a process-local rate limit and JSONL query audit
path; network binds fail closed when `EP_MCP_KEY_ALEX_HORMOZI_BRAIN` is absent.

Run the deployment preflight before handing the service to the company gateway:

```powershell
python tools/hormozi-brain/company_readiness.py
```

The preflight writes
`private-input/packs/alex-hormozi-brain-v1/meta/company-deployment-readiness.json`.
It checks the tracked network configuration, private-pack Git exclusion, OpenAI
provider selection, and secret presence without printing secret values. A
`ready_for_gateway` result still requires the company gateway to supply TLS,
identity/access policy, distributed rate limiting, and its audit sink.

## Rebuild an approved private pack

Use the inventory and rights decision tools before building. The builder only
extracts records explicitly marked `approved_internal`; it never extracts
quarantined material. See `tools/source-intake/README.md` and
`tools/private-pack-builder/README.md` for the exact commands.

For a full rebuild followed by regenerated validation artifacts, use:

```powershell
.\scripts\rebuild-hormozi-brain.ps1
```

This runs the pack builder, Paperclip skill validation, embedding-cost estimate,
official-channel transcription estimate, offline retrieval benchmark,
decision-support contract preflight, company preflight, and final acceptance
audit. It does not call the OpenAI API;
embeddings, audio, and live-agent response evaluation remain explicitly
pending until their approved inputs are supplied.

After exercising a real company agent, export one JSON object per line with
`id`, `disposition`, and `response`, then run:

```powershell
python tools/hormozi-brain/evaluate_decision_support.py `
  --responses private-input/agent-evaluations/hormozi-responses.jsonl `
  --require-responses
```

To transcribe a catalogued official video that has no captions, inject the
approved API key and pass its catalog ID explicitly:

```powershell
python tools/hormozi-brain/transcribe_official.py `
  --video-id OVhNSzFSoZs `
  --catalog private-input/packs/alex-hormozi-brain-v1/meta/official-channel-catalog.json `
  --output private-input/packs/alex-hormozi-brain-v1
```

The command downloads only temporary best-audio, sends chunk files to the
existing OpenAI transcription endpoint, writes timestamped evidence, updates
the catalog, and removes all temporary media on exit.

Before that call, use the same command with `--estimate-only` (and optionally
`--price-per-minute-usd`) to write a metadata-only request/duration estimate;
it neither downloads media nor calls OpenAI. The rebuild script also uses the
ignored `private-input/official-channel-metadata-cache.json` when a read-only
browser session has already verified durations; if that cache is absent or
stale, the estimator records the videos as pending instead of guessing.
