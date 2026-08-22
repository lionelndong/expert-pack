# Private ExpertPack Builder

This utility creates a local, source-grounded ExpertPack from a source intake
manifest and a separately reviewed rights-decision file.

Start with the policy, template, and commands in
[`config/source-intake/PRIVATE-PACK-BUILDER.md`](../../config/source-intake/PRIVATE-PACK-BUILDER.md).

## Design

- It is dry-run by default and never opens source content in that mode.
- Extraction is an explicit, two-file authorization workflow: inventory plus
  human rights decision.
- Each atom has the source ID, immutable source SHA-256, and a line-range or
  page-range locator in YAML frontmatter.
- Exact duplicate source hashes create one set of atoms and a deduplication
  record in the report.
- The output uses `concepts/` and `meta/source-coverage.md`, never `sources/`.
- It never invokes OCR. A PDF page without extractable text is reported for
  review instead of being treated as content.

Run the synthetic test suite with a Python runtime that has `pypdf` and
`reportlab`, such as the Codex bundled workspace runtime:

```powershell
& "C:\path\to\python.exe" `
  tools\private-pack-builder\test_build_private_pack.py
```
