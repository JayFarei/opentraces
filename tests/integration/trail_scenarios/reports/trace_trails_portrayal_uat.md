# Trace Trails Portrayal UAT

## Scope

Track A adds deterministic reviewer-facing coverage for two Trace Trails
portrayal paths. The test uses the existing full-stack demo helpers and a
synthetic Claude transcript; live Claude is not required.

Owned implementation:

- `tests/integration/test_trace_trails_portrayal.py`
- `tests/integration/trail_scenarios/reports/trace_trails_portrayal_uat.md`

## Reviewer Packet Shape

The test builds a concise packet with:

- `packet_version`
- `purpose`
- `fixture_mode`
- `live_claude_required`
- `scenarios`

Each scenario carries:

- stable scenario metadata
- identity fields: commit SHA, trace id, step index, Trace Patch id, Git Anchor id
- reviewer command entries with payload keys
- judgement points with a pass/fail result and compact evidence

The packet intentionally references verbose helper payload keys instead of
embedding all raw command JSON.

## Scenario 1: hook_boundary_full_stack_demo

Helper:

```text
run_demo(root, verbose=True)
```

Pass criteria:

- post-commit maturation does not create Git Anchors before session ingest
- hook-boundary filesystem observation records the generated file mutation
- watcher backstop attributes that observation to the firm step window
- maturation creates exactly one reviewer-grade Git Anchor
- `trail explain`, `blame`, `graph`, `trail search`, and `trail play` expose the same anchored trail

Reviewer commands represented in the packet:

```text
opentraces trail explain --commit <commit_sha> --project <scratch_repo> --json
opentraces trail explain --trace <trace_id> --step <step_index> --project <scratch_repo> --json
opentraces blame <commit_sha> --project <scratch_repo> --json
opentraces graph --project <scratch_repo> --json
opentraces trail search --commit <commit_sha> --project <scratch_repo> --json
opentraces trail play <trace_id> --project <opened_workspace> --json
```

## Scenario 2: installed_runtime_watcher_tick

Helper:

```text
run_installed_runtime_demo(root, verbose=True)
```

Pass criteria:

- `opentraces --json setup git` installs the Git post-commit hook
- a real `git commit` executes the installed hook and records no premature anchors
- pre-tick state has a filesystem observation but no attribution or Git Anchor
- `opentraces watcher tick --project <scratch_repo> --json` ingests the synthetic session and matures one anchor
- final event log contains observation, attribution, and exactly one Git Anchor
- `trail explain`, `blame`, `graph`, and `trail search` expose the anchored trail

Reviewer commands represented in the packet:

```text
opentraces --json setup git
opentraces watcher tick --project <scratch_repo> --json
opentraces trail explain --trace <trace_id> --step <step_index> --project <scratch_repo> --json
opentraces blame <commit_sha> --project <scratch_repo> --json
opentraces graph --project <scratch_repo> --json
opentraces trail search --commit <commit_sha> --project <scratch_repo> --json
```

## Focused Validation

```text
source .venv/bin/activate
pytest tests/integration/test_trace_trails_portrayal.py -q
```

This deterministic run does not launch Claude. It creates disposable Git
repositories, synthetic transcripts, and isolated OpenTraces state under the
pytest temp directory.

## Live Opt-In Follow-Up

Live REPL validation remains outside Track A. When an operator wants the
human-in-the-loop path, use the existing opt-in Trace Trails harness:

```text
source .venv/bin/activate
OT_REAL_REPL=1 OT_TRAIL_REAL_REPL=1 pytest tests/integration/test_trail_real_repl_scenarios.py -q -k phase7_lineage_consumers
```

That command is intentionally opt-in because it depends on a live Claude
runtime and local operator setup.
