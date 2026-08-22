# Source Intake Inventory

`inventory_sources.py` is the first, deliberately limited stage of the source
intake process. It creates an auditable JSON manifest from folders that a
human explicitly approves on the command line.

It does not copy, convert, OCR, transcribe, parse, extract, chunk, embed,
index, or otherwise make source content available to an agent. For ordinary
files, it reads raw bytes solely to calculate a SHA-256 integrity hash.

## Safety model

- Every normal record has `rights_status: pending_rights_review` and
  `use_scope: inventory_only`. A manifest never grants an agent access.
- Paths with `leaked`, `pirated`, or `crack` (case-insensitive), including a
  matching parent folder, are automatically placed only in
  `quarantined_sources`.
- Quarantined files are not hashed or opened. Their audit record has
  `hash: null`, `use_scope: quarantined_no_agent_access`, and a reason.
- Files that cannot be hashed are also blocked from access in
  `unreadable_sources`.
- The tool rejects an output file inside any source root and refuses to
  overwrite an existing manifest.
- Symbolic links are skipped to avoid accidentally traversing outside an
  approved root.

The manifest schema and a non-sensitive example are in
[`config/source-intake`](../../config/source-intake/).

## Generate profile-specific rights decisions

After an authorized reviewer has confirmed the permitted internal use, create
a separate, complete decision file with `generate_rights_decisions.py`. The
tool reads only manifest metadata and refuses to overwrite a decision file or
write one into an approved source root.

`evidence` selects non-quarantined records only from the reviewed raw and
book-library roots, while keeping Paperclip skill artifacts and raw-library
navigation Markdown out of that pack. `skills` selects only curated Paperclip
Markdown outside a `source/` directory. Every other normal record is
explicitly marked `excluded`; inventory-quarantined and unreadable records
remain quarantined in both profiles. Use it only when the named reviewer has
authorized the whole selected scope; otherwise author the source decisions
manually.

```powershell
python tools\source-intake\generate_rights_decisions.py `
  --manifest "C:\safe\hormozi-source-manifest.json" `
  --profile evidence `
  --approved-by "designated-rights-holder" `
  --approved-at 2026-08-22 `
  --evidence "Documented company authorization for internal agent retrieval." `
  --out "C:\safe\hormozi-evidence-rights-decisions.json"
```

Run it once for `evidence` and once for `skills`, using a different new output
file for each. The profile generator never extracts source content and does
not override a manifest quarantine.

## Run an inventory

Use an output location outside the source folders. In this project,
`private-input/` is ignored by Git and is a suitable local-only place for a
real manifest after creating a subdirectory such as `private-input/inventory`.

```powershell
New-Item -ItemType Directory -Force private-input\inventory | Out-Null

python tools\source-intake\inventory_sources.py `
  --approved-root "C:\\approved-sources\\ExportBlock\\Private & Shared\\Alex Hormozi Knowledge Library" `
  --approved-root "C:\\approved-sources\\book-to-skill-master\\ALEX HORMOZI" `
  --approved-root "C:\\approved-sources\\paperclip.ai\\alex-hormozi-skills" `
  --out "private-input\\inventory\\hormozi-source-manifest.json"
```

Use the project's virtual environment if it is active; the script itself uses
only the Python standard library.

You may add organisation-specific terms while retaining the mandatory default
terms:

```powershell
python tools\source-intake\inventory_sources.py `
  --approved-root "C:\\approved-root" `
  --suspect-term "confidential" `
  --out "C:\\safe-output\\source-manifest.json"
```

## What happens next

1. A designated rights holder reviews every `inventory_only` record and the
   evidence for its permitted use.
2. Material whose ownership, licence, confidentiality, or terms are unclear
   stays out of the agent. A quarantined inventory record cannot be approved
   by a rights-decision file; correct a false classification only through a
   new, documented inventory and review.
3. Only then should a separately reviewed process extract source-grounded
   atoms into a private pack, preserving the manifest `source_id` and hash as
   provenance.

Do not use this inventory manifest as a retrieval corpus or an authorization
record.
