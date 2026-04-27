# Trace Trails Phase 7 UAT Results - 2026-04-27

## Scope

This report covers the Phase 7 "lineage consumers plus trail search" happy-path UAT scenario. The same real Trace Patch was exercised through `trail explain`, `trail resolve`, `blame`, `graph`, and `trail search`.

Progress was committed before this UAT layer:

- Root repo: `2a56330c7 Implement Trace Trails phase 7 consumers`
- `kb`: `fb60108 Document Trace Trails phase 7 implementation`

## Scenario Assets

- Scenario file: `tests/integration/trail_scenarios/phase7_lineage_consumers.toml`
- Report template: `tests/integration/trail_scenarios/reports/phase7_uat_report_template.md`
- Direct live evidence bundle: `/tmp/ot-phase7-uat-bundles/phase7_lineage_consumers-20260427T064902Z.json`
- Pytest-wrapper evidence bundle: `.pytest_cache/trail_uat/phase7_lineage_consumers-20260427T064755Z.json`
- Scratch repo retained for reviewer command capture: `/tmp/ot-h-phase7_lineage_consumers`

The scenario is hook-only. It does not require the watcher backstop; no `capture_limitations` beyond hook-based real REPL capture were observed in the passing run.

## Verification Commands

```text
$ ./otd --version
otd, version 0.4.0
```

```text
$ source .venv/bin/activate && ruff check tests/integration/harness/attribution_v2_harness.py tests/integration/test_trail_real_repl_scenarios.py
All checks passed!
```

```text
$ source .venv/bin/activate && pytest tests/cli/test_trail_search_phase7.py tests/integration/test_trail_real_repl_scenarios.py -q
........sssss                                                            [100%]
8 passed, 5 skipped in 5.82s
```

```text
$ source .venv/bin/activate && OT_REAL_REPL=1 OT_TRAIL_REAL_REPL=1 pytest tests/integration/test_trail_real_repl_scenarios.py -q -k phase7_lineage_consumers
.                                                                        [100%]
1 passed, 4 deselected in 32.26s
```

```text
$ source .venv/bin/activate && OT_REAL_REPL=1 OT_TRAIL_REAL_REPL=1 python tests/integration/harness/attribution_v2_harness.py --evidence-dir /tmp/ot-phase7-uat-bundles --keep -v tests/integration/trail_scenarios/phase7_lineage_consumers.toml
phase7_lineage_consumers -> /tmp/ot-h-phase7_lineage_consumers
trail explain --commit HEAD patch count 1 passed
trail explain --trace/--step anchored_in_git tracepatch-sha256:d4828634a1055de899ee3ebf902291300682b5cd5c52b3e2f8bc42102661ed79 passed
trail explain --commit 0c35a65a includes selected patch passed
trail explain src/phase7_lineage_consumer.py:1 resolves selected patch passed
trail resolve ot:// refs all resolved passed
trail explain/resolve, blame, graph, and search agree on tracepatch-sha256:d4828634a1055de899ee3ebf902291300682b5cd5c52b3e2f8bc42102661ed79 passed
evidence bundle /tmp/ot-phase7-uat-bundles/phase7_lineage_consumers-20260427T064902Z.json
1/1 passed
```

## User-Facing Command Output

```text
$ otd trail explain --commit HEAD
Commit 0c35a65a42d9
  30ea6be4-4deb-4372-8daa-8307ed16eddb tracepatch-sha256:d4828634a1055de899ee3ebf902291300682b5cd5c52b3e2f8bc42102661ed79 exact_range_hash
```

```text
$ otd blame HEAD
Trace Trails (canonical event log evidence)

  t:30ea6be4  src/phase7_lineage_consumer.py:1  exact_range_hash (firm)
    Trace Patch: tracepatch-sha256:d4828634a1055de899ee3ebf902291300682b5cd5c52b3e2f8bc42102661ed79
    Git Anchor:  gitanchor-sha256:6f620e336b9d7b001a481a0215a68fdba5975783d1e5666d63f9ca7565f3ae65

  explain: otd trail explain --commit 0c35a65a42d9a9717f23bfe48ee56f1c9e6113db
```

```text
$ otd graph --limit 1 --no-color
┊╭┄t:30ea6be4 [2 lines]
┊●   c:0c35a65a  real trace trail phase 7 lineage consumers
├╯
```

```text
$ otd trail search --commit HEAD
Trace Trail search: anchors_per_commit (1 result(s))
  tracepatch-sha256:d4828634a1055de899ee3ebf902291300682b5cd5c52b3e2f8bc42102661ed79 0c35a65a42d9 src/phase7_lineage_consumer.py exact_range_hash
```

```text
$ otd trail search --trace 30ea6be4-4deb-4372-8daa-8307ed16eddb
Trace Trail search: patches_per_trace (1 result(s))
  tracepatch-sha256:d4828634a1055de899ee3ebf902291300682b5cd5c52b3e2f8bc42102661ed79 src/phase7_lineage_consumer.py exact_range_hash
```

```text
$ otd trail search --path src/phase7_lineage_consumer.py
Trace Trail search: patches_touching_file (1 result(s))
  tracepatch-sha256:d4828634a1055de899ee3ebf902291300682b5cd5c52b3e2f8bc42102661ed79 0c35a65a42d9 src/phase7_lineage_consumer.py exact_range_hash
```

```text
$ otd trail search --survival reverted
No Trace Trail results
```

## Machine Evidence

The direct live evidence bundle reported:

```json
{
  "otd_version": "otd, version 0.4.0",
  "hook_interpreter": "#!/usr/bin/env sh",
  "commit_sha": "0c35a65a42d9a9717f23bfe48ee56f1c9e6113db",
  "trace_id": "30ea6be4-4deb-4372-8daa-8307ed16eddb",
  "step_id": "step_4",
  "trace_patch_id": "tracepatch-sha256:d4828634a1055de899ee3ebf902291300682b5cd5c52b3e2f8bc42102661ed79",
  "git_anchor_id": "gitanchor-sha256:6f620e336b9d7b001a481a0215a68fdba5975783d1e5666d63f9ca7565f3ae65",
  "containing_segment_id": "traceslice-sha256:0da9a18bb1b9fa6c5044afd73adb9317943735ee30d835776b959fbc9e2d2798",
  "file_path": "src/phase7_lineage_consumer.py",
  "file_line_origin": {
    "path": "src/phase7_lineage_consumer.py",
    "line": 1
  }
}
```

`blame HEAD --json`, `blame src/phase7_lineage_consumer.py:1 --json`, `graph --json`, and `trail search --commit HEAD --json` all returned the same identity tuple above. The bundle stores those raw payloads under:

- `raw_json.phase7:blame:selected`
- `raw_json.phase7:blame-line:selected`
- `raw_json.phase7:graph:selected`
- `raw_json.phase7:search:selected`
- `raw_json.phase7:resolve:selected`
- `raw_json.phase7:commit:selected`

## Usability Notes

- Human: `blame HEAD` is the clearest reviewer surface because it shows the file line, evidence tier, firmness, Trace Patch id, Git Anchor id, and the exact `trail explain` follow-up command.
- Human: `graph --limit 1 --no-color` is compact and useful for navigation, but it intentionally does not print patch ids. Use `graph --json` when patch identity must be machine-compared.
- Machine: the evidence bundle now separates observed Claude session ids from canonical trace ids and includes a top-level `identities.selected` summary.
- Machine: `trail explain --commit --json` carries file path plus affected range for the origin; `blame`, `graph`, and `trail search` expose an explicit `line_origin` resource ref.
- Search: live happy-path UAT covers `--trace`, `--commit`, and `--path`. The reverted Patch Trail query is covered by deterministic fixture coverage in `tests/cli/test_trail_search_phase7.py::test_trail_search_finds_reverted_patch_trails`.
