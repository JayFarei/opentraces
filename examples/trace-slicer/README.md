# Trace Slicer Library — soft-evidence panel

On-demand conformance + utility evidence for the four trace slicers (`opentraces trace partition --by s1|s2|s3|s4`). **This is advisory and is NOT a CI gate** — the CI gate is the deterministic `slicer-conformance` otbox journey (tiling + boundary pins + mutation kills), which is hard-wired into `SENTINEL_JOURNEYS`. This panel is the soft, qualitative counterpart: it tells you whether the slicings are *useful*, not just *valid*.

## Run it

```bash
# mechanical-only (no LLM, no credentials): tiling validity + segmentation
# density + single-trajectory / S1 control-leak counts over real bucket traces.
make slicer-soft-evidence                 # 40-trace sample
make slicer-soft-evidence SAMPLE=120      # bigger sample

# add the advisory LLM pass (sentiment conformance + 3-persona utility),
# routed through core.llm_provider.detect_provider() — same backend the
# cheap-LLM slicers use as their `provider` judge.
make slicer-soft-evidence LLM=1
```

or directly:

```bash
python examples/trace-slicer/soft_evidence.py --sample 40 [--llm] [--out report.md]
```

## What it reports

Per slicer: tiling validity rate (always 1.0 by construction), mean trajectories per 100 steps, single-trajectory (over-coarse) count, the S1 control-message leak count (control families the LOCKED S1 blocklist does not cover), and — with `--llm` — mean sentiment conformance plus mean utility through an ML engineer / observability engineer / evaluation engineer's eyes.

## Provenance

The full Phase-0 stratified panel (200-trace mechanical sweep + a 36-trace 3-persona LLM panel that drove the GO/NO-GO verdict for all four slicers) lives at `experiments/slicer-prototype/REPORT.md` (+ `report.html`). This example re-runs the same shape on demand against the *shipped* slicers so the soft evidence stays reproducible after Phase 0.
