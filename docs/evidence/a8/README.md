# A8 tie-back acceptance evidence

Independent evidence run at product head `376cfc3950b76e1c291ae42b8f33a7d09d3fa516`.

- Canonical run: `run_20260715T005147520253Z_4639683dffd8`
- Invoking session trace: `trace-a8-acceptance-captured-001`
- Run-derived trace: `f3375ddf-d2ee-5249-afb8-35a17fc296cb`
- Label: `lbl_7572fc31c952f0accec11bc284a0c32fa685e47ac58d20bbb0a788a506242bad`
- Complete-run digest: `sha256:bd9dd7bf28be26c8b3946e59c3f30c260c1d36ace99ec0ba038ba91e785203fa`
- Evidence-page HTML digest: `sha256:d4b759cec0ae92d15f5e013eb60273260917c6a63bc2de1d7b152667c3790677`
- Screenshot digest: `sha256:83e459ce617665978bde3fb772ab64888a39eb8c3090097a712acae2484da1c2`

The screenshot is `a8-acceptance-page.png`; the sanitized result excerpt is
`result-excerpt.json`. The invoking session used the repository's accepted
capture-ingest harness over the real bench output; the bench itself executed on
the local-container crabbox provider. Re-ingestion produced exactly one
byte-identical label, and a one-byte mutation of a copied stored run was refused
with `RunIntegrityError: finalized file changed: source/scenario.py`.

Known non-blocking presentation finding: the page fact strip does not wrap the
full product pin and execution digest cleanly at desktop widths. The screenshot
keeps the actual rendering; no CSS or evidence was altered. Follow-up:
https://github.com/JayFarei/opentraces/issues/330.
