# Private Pack Builder

`tools/private-pack-builder/build_private_pack.py` is the second, separate
stage after `tools/source-intake/inventory_sources.py`. It turns only
explicitly approved, source-hashed local files into a local ExpertPack.

It does not make a rights decision. A designated rights holder must first
review the inventory manifest and create a separate decisions file from
[`rights-decisions.template.json`](rights-decisions.template.json).

## Hard boundaries

- The default command is a dry run. It reads only the JSON manifest and
  decisions file, reports what *would* happen, and creates no pack.
- Content extraction requires the exact `--allow-content-extraction` flag.
- A source is eligible only when its decision is `approved_internal` with a
  documented approver, date, internal-use scope, and evidence.
- `excluded` and every `quarantined_*` decision are never extracted.
- Records in the inventory's `quarantined_sources` and `unreadable_sources`
  lists are always blocked. A decision file cannot reclassify them.
- Before extracting a source, the builder re-hashes the local bytes and
  requires an exact match with the inventory hash. It refuses symlinks and
  paths outside the inventory's approved roots.
- Markdown and UTF-8/UTF-16-BOM text files are supported. PDFs are read only
  when a text layer is available; scan-only pages are reported as OCR-required
  and are not silently ingested. EPUB, audio, video, Office documents, and
  archives are reported as unsupported rather than parsed.
- A content build with any approved PDF fails before publishing a pack if its
  runtime lacks `pypdf`; it never silently creates a PDF-incomplete pack.
- Output is a new local directory only. The builder refuses to overwrite a
  directory or write into a source root. It does not create `sources/`; source
  evidence lives in source-grounded `concepts/` atoms and `meta/source-coverage.md`.

## Review and dry run

Keep real manifests and decisions under the ignored `private-input/` folder.
Do not commit them.

```powershell
Copy-Item config\source-intake\rights-decisions.template.json `
  private-input\inventory\hormozi-rights-decisions.json

python tools\private-pack-builder\build_private_pack.py `
  --manifest private-input\inventory\hormozi-source-manifest.json `
  --rights-decisions private-input\inventory\hormozi-rights-decisions.json `
  --report private-input\inventory\hormozi-private-pack-dry-run.json
```

Review the report before extracting anything. In particular, resolve every
`configuration_error`, `hash_mismatch`, and `ocr_required` result deliberately.

## Build a local pack after review

Install the small PDF reader dependency in the Python environment that will
run the tool if it is not already available:

```powershell
.\.venv\Scripts\python.exe -m pip install -r tools\private-pack-builder\requirements.txt
```

Create the parent output directory first. The final output path itself must
not already exist.

```powershell
New-Item -ItemType Directory -Force private-input\packs | Out-Null

python tools\private-pack-builder\build_private_pack.py `
  --manifest private-input\inventory\hormozi-source-manifest.json `
  --rights-decisions private-input\inventory\hormozi-rights-decisions.json `
  --allow-content-extraction `
  --output private-input\packs\alex-hormozi-private `
  --pack-name "Alex Hormozi - approved internal sources" `
  --pack-slug alex-hormozi-private `
  --report private-input\inventory\hormozi-private-pack-build-report.json
```

The output should be validated before any MCP index is built:

```powershell
.\.venv\Scripts\expertpack.exe validate private-input\packs\alex-hormozi-private --strict
```

The resulting pack is source-grounded reference material. It is not a claim
that a public figure personally endorses, currently holds, or authorized the
agent's decisions.
