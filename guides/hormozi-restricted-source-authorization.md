# Restricted source authorization record

The two inventory records named `LEAKED_Pricing_Playbook.pdf` are intentionally
quarantined. They must not be opened, hashed, OCRed, summarized, embedded, or
served until the company owner records authorization here or in an equivalent
internal ticket.

The currently quarantined source IDs are:

- `qsrc-6c6e3045f49e5555`
- `qsrc-01130c01e9dc5522`

The machine-readable record must conform to
[`config/source-intake/restricted-authorization.schema.json`](../config/source-intake/restricted-authorization.schema.json).
Start from the tracked
[`config/source-intake/restricted-authorization.template.json`](../config/source-intake/restricted-authorization.template.json),
replace every placeholder, and save the completed record only under the
ignored `private-input/restricted-processing/authorization.json` path (or an
approved internal ticket location). Do not commit the completed record.

For each copy, record:

- inventory `source_id`;
- named rights owner and department;
- how the company obtained the file;
- confirmation that internal processing, retention, and agent retrieval are
  permitted;
- any scope limits or expiration date;
- approver identity, date, and ticket/reference ID.

Once both records are authorized, run the restricted-source intake as a
separate reviewed change: hash both copies, compare duplicate status, process
only one unique work, run OCR and visual QA, then update the coverage ledger.
If authorization is denied or remains unresolved, leave both records in
quarantine and report the gap explicitly.

The fail-closed processor is
`tools/hormozi-brain/process_restricted.py`. It refuses to open or hash either
file until the authorization record contains both source IDs, rights owner,
scope, approver, date, and ticket. After approval, add `--run-ocr` to execute
the existing page-level OCR and visual-QA workflow for each exact-unique work.
