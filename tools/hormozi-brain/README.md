# Hormozi brain tooling

All generated content belongs under the ignored `private-input/` directory.

Build the composite local pack from the reviewed inventory:

```powershell
python tools/hormozi-brain/build_brain.py
```

The builder accounts for all 428 inventory records, normalizes the supplied
transcript exports into 273 unique videos and timestamp-bounded atoms, extracts
the authorized EPUB, records audio metadata (and ingests approved timestamped
transcription JSON when present), and mirrors the 24 Paperclip packages under
`private-input/skills/alex-hormozi/`. The two records named
`LEAKED_Pricing_Playbook.pdf` remain quarantined until authorization is
documented.

Optional follow-up adapters:

```powershell
python tools/hormozi-brain/quality_check.py
python tools/hormozi-brain/fetch_official.py --channel-url https://www.youtube.com/@AlexHormozi/videos --fetch-captions
python tools/hormozi-brain/run_ocr_batch.py
python tools/hormozi-brain/qa_ocr_results.py
python tools/hormozi-brain/make_ocr_contact_sheet.py
python tools/hormozi-brain/acceptance_audit.py
python tools/hormozi-brain/validate_skills.py
python tools/hormozi-brain/transcribe_audio.py --audio <approved-audio> --output private-input/audio-estimates/plan.json --estimate-only
python tools/hormozi-brain/transcribe_audio.py --audio <approved-audio> --output private-input/audio-transcriptions/audio.json
python tools/hormozi-brain/build_brain.py --audio-transcriptions-dir private-input/audio-transcriptions
```

The non-estimate command writes only timestamped JSON to the ignored
`private-input/audio-transcriptions/` directory. The builder matches each JSON
record to an approved manifest audio path, writes a cited atom under `audio/`,
and marks the corresponding ledger record included. Unmatched or malformed
outputs remain explicitly reported and are not ingested.

For the complete authorized finalization flow, use the repository wrapper:

```powershell
.\scripts\rebuild-hormozi-brain.ps1 -RunOpenAIGates
```

That switch is required before any OpenAI transcription call; the default
rebuild only estimates usage and never calls the API. Restricted processing is
separate and requires an authorization record:

```powershell
.\scripts\rebuild-hormozi-brain.ps1 `
  -RestrictedAuthorizationPath private-input/restricted-processing/authorization.json `
  -RunRestrictedOcr
```

The OCR batch is driven only by the evidence report's approved page list,
retains rendered PNGs for visual QA, records confidence, distinguishes true
blank pages, and can repair malformed PDF indexes in a temporary copy through
`pikepdf`; originals are never rewritten. Low-confidence pages remain marked
for human review until an owner signs off. Install the optional Python pieces
from `tools/hormozi-brain/requirements-ocr.txt` alongside Poppler and Tesseract.

`quality_check.py` is an offline lexical/provenance smoke test. It does not
pretend to measure semantic embedding quality. Vector indexing requires
`OPENAI_API_KEY` and uses the direct OpenAI provider in the pinned EP MCP
runtime.
