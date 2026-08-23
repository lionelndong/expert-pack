# Hormozi brain tooling

All generated content belongs under the ignored `private-input/` directory.

Build the composite local pack from the reviewed inventory:

```powershell
python tools/hormozi-brain/build_brain.py
```

The builder accounts for all 428 inventory records, normalizes the supplied
transcript exports into 273 unique videos and timestamp-bounded atoms, extracts
the authorized EPUB, records audio metadata, and mirrors the 24 Paperclip
packages under `private-input/skills/alex-hormozi/`. The two records named
`LEAKED_Pricing_Playbook.pdf` remain quarantined until authorization is
documented.

Optional follow-up adapters:

```powershell
python tools/hormozi-brain/quality_check.py
python tools/hormozi-brain/fetch_official.py --channel-url https://www.youtube.com/@AlexHormozi/videos --fetch-captions
python tools/hormozi-brain/ocr_sources.py --pdf <approved.pdf> --pages 6 7 --output private-input/ocr/<source>
python tools/hormozi-brain/transcribe_audio.py --audio <approved-audio> --output private-input/transcripts/audio.json
```

`quality_check.py` is an offline lexical/provenance smoke test. It does not
pretend to measure semantic embedding quality. Vector indexing requires
`OPENAI_API_KEY` and uses the direct OpenAI provider in the pinned EP MCP
runtime.
