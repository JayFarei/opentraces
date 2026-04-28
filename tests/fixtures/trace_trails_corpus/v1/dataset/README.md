# Trace Trails Corpus v1

This deterministic fixture is generated from the Trace Trails full-stack demo.
It is intentionally small and synthetic: one TraceRecord row, canonical
TrailEvents, selected command/API payloads, and a Trace Workspace manifest
without a binary Git bundle.

Regenerate it with:

```bash
.venv/bin/python tests/integration/harness/trace_trails_corpus.py --update
```
