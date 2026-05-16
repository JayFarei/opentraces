# otbox — snapshottable full test environment

## What otbox is

otbox seeds a fully-populated opentraces world (initialised project,
captured agent sessions, real git repo, built Trace Index, bound fake
HF remote), **snapshots** it as a content-addressed checkpoint, and
forks any number of declarative TOML journeys off it in seconds. It is
a dev/CI tool, not a shipped product surface, and intentionally absent
from `opentraces --help`. Lifecycle vocabulary is borrowed from
[crabbox](https://crabbox.sh), opentraces-specific. Governing specs:
`kb/plans/060` (Tier 0), `061` (Tier 1 SSH/Tailscale), `062`
(checkpoint × journey matrix), `064` (agent-centric session UAT), `068`
(credible world-state substrate), `069` (TOML preconditions + tiered
coverage gate), `070` (agent-facing journey backfill), `071`
(simulated-user runner + capture pipeline), `072` (checkpoints from
captured artifacts).

Bootstrap a new repo with `./otbox init` — writes `.otbox.yaml`
defaults and the agent skill at `.agents/skills/otbox/SKILL.md`.

## Quick start

```bash
./otbox up --seed smoke                              # provision a box + seed a world
./otbox snapshot smoke-base                          # freeze it to a tar archive
./otbox down                                         # tear the box down (zero residue)
./otbox up --from smoke-base                         # restore a fresh box, fast
./otbox journey cli-publish-happy-path               # run a CLI journey -> PASS
./otbox journey agent-session-trail-explain-happy    # gold journey on c-captured-real-session
./otbox artifacts                                    # bundle run evidence for a PR
./otbox down --all                                   # clean up
```

Every command takes `--json` for stable machine-readable output (so an
agent can drive otbox without reading source).

The gold journey `agent-session-trail-explain-happy` forks from the
`c-captured-real-session` checkpoint (artifact-preferred,
synthetic-fallback). It exercises the real `trail explain` consumer
API against a real captured Claude Code session and asserts the full
evidence chain: `relation = "anchored_in_git"`, real `trace_patch_id`,
real `git_anchor_id`, `event_log_ref = "refs/opentraces/local/events/v1"`.

## Commands

| Command | Purpose |
|---|---|
| `init [--force]` | write `.otbox.yaml` + the agent SKILL into this repo |
| `up [--seed S] [--from SNAP] [--driver D] [--id ID]` | provision (or restore) a box, make it current |
| `warmup [--driver D] [--id ID]` | provision a Tier 1 box (no seed) — for reuse |
| `sync [--box ID] [--full-resync]` | rsync the working tree into the box (Tier 1) |
| `seed <scenario> [--box ID]` | materialize a seeded world in a box |
| `snapshot <name> [--box ID] [--overwrite]` | freeze a box to `.otbox/snapshots/<name>.tar.gz` |
| `restore <name> [--driver D] [--id ID]` | fork a fresh box from a snapshot |
| `down [--box ID] [--all]` | tear a box down — leaves zero host residue |
| `run [--box ID] -- <cmd...>` | run an arbitrary command inside a box |
| `ssh [--box ID] [--root]` | drop into the box's project dir (Tier 0 or Tier 1) |
| `journey <name> [--box ID] [--artifacts]` | run a catalogue journey, verdict PASS/FAIL/SKIP |
| `matrix [filters] [--inventory [--strict]]` | run the (journey × base-checkpoint) matrix |
| `capture-refresh --scenario <name> [--dry-run] [--base-checkpoint C]` | drive a simulated-user scenario and snapshot the result (plan 071) |
| `artifacts [--box ID] [--label L]` | bundle journey-run evidence for a PR |
| `status [--box ID]` / `list` | inspect boxes, snapshots, drivers, seeds, journeys |
| `snapshot-rm <name>` | delete a snapshot |
| `image build [--tag T]` | build the Linux runtime image for the `docker` driver |

## Architecture

```
otbox  (repo-root shim, like otd)
  -> python -m tests.otbox
       cli.py              argparse dispatch, --json, capture-refresh
       env.py              box layout, isolated env, CLI-entrypoint resolution
       drivers/            substrate behind one Driver protocol
         local.py          DEFAULT Tier 0 — HOME-isolated filesystem sandbox
         docker.py         opt-in Tier 0 — containerized (needs `otbox image build`)
         remote.py         opt-in Tier 1 — SSH-lease stub, gated by OT_OTBOX_TIER1
       seed.py             seeded-world builders (smoke, world)
       snapshot.py         portable workspace-archive snapshot/restore
       journey.py          declarative TOML journey runner (preconditions + tier_label)
       matrix.py           (journey × checkpoint) sweep driver
       artifacts.py        PR-ready evidence bundles
       inventory.py        Click registry × journey ownership + tier rollup
       jtbd.py             plan 063 SSoT + AGENT_FACING_TRAJECTORIES_MIN_GOLD gate
       checkpoints/        named, resumable starting states + provides=...
         _empty / _prereqs / _installed_source
         _captured_session / _captured_with_revert / _captured_with_secrets
         _captured_multi_skill / _captured_with_pr_branch
         _captured_helpers.py   artifact-restore + audit re-derivation primitives
       simulated_users/    PTY/tmux capture pipeline (plan 071)
         runner.py            PTY runner — Turn / ScenarioResult / run_simulated_session
         scenario.py          TOML schema + load_scenario + scenario_digest
         _echo_binary.py      synthetic interactive REPL for default-CI meta-tests
         scenarios/*.toml     declarative scenarios (echo-meta, add-helper-function)
         templates/<name>/    initial-state filesystems copied into box.project
       captures/            committed real-agent artifacts (snapshot.tar.gz + metadata.json)
       catalogue/
         journeys/*.toml      the journey catalogue
         journey-inventory.md generated Click × journey × tier rollup
       fake_harnesses/claude   Python executable that drives live opentraces hooks
       fixtures/sessions/<name>/   per-session corpora consumed by the harness
```

A **box** is one isolated opentraces world under `.otbox/boxes/<id>/`
(`home/` = isolated `HOME`, `project/` = the seeded git repo,
`fake-remote/` = the fake HF remote). Teardown is one `rmtree`; the
developer's real `~/.opentraces`, shell profile, and git config are
never touched — `env.py::isolated_env` is the single chokepoint that
guarantees that.

## Tiers and drivers

- **Tier 0 — `local` (default).** Offline, deterministic, no Docker.
  The opentraces CLI runs as a real subprocess against the repo
  `.venv`. This is what CI runs and what the autonomous delivery
  contract verifies.
- **Tier 0 — `docker` (opt-in).** Same box layout, stronger isolation;
  the CLI runs inside a container. Needs a prebuilt Linux image —
  `otbox image build` (one-time, needs network).
- **Tier 1 — `remote` (opt-in).** SSH-lease adapter stub for journeys
  Tier 0 cannot honestly cover (real-REPL UATs, `brew install` on real
  macOS, VNC visual QA). Hard-gated behind `OT_OTBOX_TIER1=1`; never
  runs in default CI.

## Seeds

| Seed | World it builds |
|---|---|
| `smoke` | git project + one commit + `init` + 3 replayed traces + built Trace Index |
| `world` | `smoke` + git post-commit hook + a second commit (for trail/blame journeys) |

Seeds are imperative Python builders (`seed.py`) and reuse
`tests/e2e/_smoke_helpers.trace_record` rather than hand-rolling trace
payloads.

## Checkpoint × journey matrix (plan 062)

Every journey TOML declares `from_checkpoints = [...]` naming the
resumable starting state(s) it runs from (or relies on a `[preconditions]`
block — see below). The matrix runner builds each checkpoint once per
run, snapshot-forks it for every dependent journey, and emits a
coverage row.

```bash
./otbox matrix                                # run the whole matrix
./otbox matrix --journey 'install*'           # filter by name
./otbox matrix --checkpoint 'c-installed-*'   # filter by base checkpoint
./otbox matrix --lane core                    # only core-lane journeys
./otbox matrix --inventory                    # rebuild Click × journey map
./otbox matrix --inventory --strict           # fail on plan 063 SSoT + 069 tier drift
```

Bundled checkpoints today (plan 069 R3 adds the `provides=...` column):

| Name | Built by | What it adds | `provides=...` |
|---|---|---|---|
| `c-empty` | builder | a bare provisioned box, nothing installed | `{}` |
| `c-prereqs-present` | composed_from `c-empty` | + verified python3.10+, git, rsync | `{}` |
| `c-installed-source` | composed_from `c-prereqs-present` | + opentraces installed editable in `box.project/.testvenv` | `{}` |
| `c-captured-real-session` | composed_from `c-installed-source` | + real captured Claude session (16 TrailEvents, matured Git Anchor, populated Trace Index) | `captured_traces=1, survival_states=["alive_on_path"], branch_commits=1` |
| `c-captured-with-revert` | composed_from `c-captured-real-session` | + `git revert <captured>` + watcher tick + `trail mature` | `captured_traces=1, survival_states=["reverted","lost","alive_transformed","unknown"], branch_commits=2` |
| `c-captured-with-secrets` | composed_from `c-installed-source` | + captured session whose Edit writes fake sk-test / ghp_ tokens (regex + entropy fire) | `captured_traces=1, survival_states=["alive_on_path"], branch_commits=1, has_security_findings=True` |
| `c-captured-multi-skill` | composed_from `c-installed-source` | + 3 sessions across `skill-alpha`/`skill-beta` (3 trace_ids, 3 commits) | `captured_traces=3, survival_states=["alive_on_path"], skills=["skill-alpha","skill-beta"], branch_commits=3` |
| `c-captured-with-pr-branch` | composed_from `c-captured-real-session` | + branches `feat/pr-branch-test` + 2 more captured commits via session-id rewriting | `captured_traces=3, survival_states=["alive_on_path"], branch_commits=3` |

Snapshots cache content-addressed at
`.otbox/snapshots/_checkpoint-<name>-<hash>.tar.gz` (gitignored).
Editing a checkpoint builder invalidates the cache for that checkpoint
and all descendants.

Inventory: `make otbox-inventory` regenerates
`tests/otbox/catalogue/journey-inventory.md`, the Click registry ×
journey-ownership × tier-rollup map. The plan-045 / plan-062 /
plan-069 coverage gate consumes this file.

## Journeys

A journey is a declarative TOML scenario document under
`catalogue/journeys/`. The runner is generic — **add coverage by adding
a `.toml` file, not by editing harness code.** The schema extends the
plan-045 release-UAT format with plan-062's `from_checkpoints`,
plan-069's `[preconditions]` block + `tier_label`, and otbox's `tier`
+ `seed`:

```toml
name = "agent-session-trail-explain-happy"
description = "..."
lane = "core"                                  # core | extended | diagnostic
tier = 0                                       # 0 = local/docker, 1 = remote lease
tier_label = "gold"                            # bronze | silver | gold (plan 069 R4)
trajectories = ["build-dataset-from-lineage"]  # plan 063 trajectory slugs
persona = "agent"
requires = ["cli", "git"]                      # capability gate (cli, git, tmux, tier1, real_repl)
from_checkpoints = ["c-captured-real-session"] # plan 062 — explicit pin

# Plan 069 R1: declarative world-state needs. Any checkpoint whose
# `provides=...` satisfies every key here can host this journey; when
# `from_checkpoints` is also set, the runner validates that the pin
# satisfies the preconditions and SKIPs with a clear conflict reason
# if they disagree.
[preconditions]
min_captured_traces = 1
# requires_survival_states = ["reverted"]
# requires_skills = ["skill-alpha", "skill-beta"]
# requires_branch_commits_min = 2
# requires_security_findings = true

[[steps]]
type = "cli"           # cli | shell | write_file | service | http_get | tmux | sync
id = "explain-edit-step"
argv = ["trail", "explain", "--trace", "{trace_id}", "--step", "{step_index}", "--json"]

[[assertions]]
kind = "stdout_json"   # returncode | stdout_contains | stderr_contains
step = "explain-edit-step"   #   stdout_json | path_exists | file_count_min
path = "relation"
equals = "anchored_in_git"
```

Step path templating: `{project}`, `{home}`, `{fake_remote}`,
`{state_dir}`, `{opentraces_dir}`, `{box_root}`, `{box_id}`,
`{repo_root}`, `{port}`. Captured-session checkpoints additionally
expose `{trace_id}`, `{session_id}`, `{commit_sha}`, `{step_index}`,
`{transcript_path}`, and (for `c-captured-with-pr-branch`)
`{branch_name}`, `{base_commit_sha}`, `{head_commit_sha}`,
`{branch_commit_count}`.

Precondition resolver semantics (plan 069 R2 / R8):
- An empty `[preconditions]` block defaults to today's behaviour
  (capability gate + explicit `from_checkpoints` pin).
- `resolve_precondition_match()` walks the checkpoint registry in
  sorted order and returns the first checkpoint whose `provides`
  satisfies every declared key. Because sort order matters when
  multiple checkpoints qualify, journeys that want a specific
  substrate (e.g. the smaller `c-captured-real-session` over the
  alphabetical first match `c-captured-multi-skill`) keep an explicit
  `from_checkpoints` pin.
- When both are declared, the explicit pin wins but MUST satisfy the
  preconditions; otherwise the journey SKIPs with
  `"precondition conflict: ..."`.

Current catalogue highlights (full sweep: `make otbox-journeys` runs
55 PASS):

| Journey | Lane | Tier | Tier label | Surface |
|---|---|---|---|---|
| `cli-publish-happy-path` | core | 0 | bronze | CLI: init → dataset → publish to fake remote |
| `cli-lifecycle` | core | 0 | bronze | CLI: status / config / trace get / remove |
| `trace-map-and-slice` | core | 0 | bronze | CLI: trace index / query / map / slice |
| `trail-blame-and-graph` | core | 0 | bronze | CLI: backfill / trail blame / trail graph |
| `bucket-inspect` | core | 0 | bronze | CLI: bucket status / manifest |
| `agent-session-trail-explain-happy` | core | 0 | gold | `trail explain` on `c-captured-real-session` (plan 064 slice) |
| `survival-walk-reverted` | core | 0 | gold | `trail track / search / explain` on `c-captured-with-revert` |
| `pr-blame-on-captured-branch` | core | 0 | gold | `trail blame pr render` on `c-captured-with-pr-branch` |
| `security-sanitize-captured-content` | core | 0 | gold | sanitize pipeline on `c-captured-with-secrets` |
| `dataset-sync-skill-history` | core | 0 | gold | `dataset schedule` lifecycle on `c-captured-multi-skill` |
| `doctor-health` | extended | 0 | bronze | CLI: doctor JSON health |
| `web-viewer-smoke` | extended | 0 | bronze | Web: Flask review backend, headless |
| `tui-review-smoke` | extended | 0 | bronze | TUI: lazytraces in tmux |
| `install-smoke-tier1` | extended | 1 | bronze | cross-OS install smoke (opt-in) |

## Captured-session checkpoints — artifact-preferred, synthetic-fallback

The captured-session checkpoint family (`c-captured-real-session`,
`c-captured-with-revert`, `c-captured-with-secrets`,
`c-captured-multi-skill`, `c-captured-with-pr-branch`) resolves
through `restore_from_capture()` in
`tests/otbox/checkpoints/_captured_helpers.py` (plan 072 R1). Each
delta starts with:

```python
def _captured_session_delta(driver, box):
    cap_meta = restore_from_capture(driver, box, "c-captured-real-session")
    if cap_meta is not None:
        box.notes["c_captured_session_audit"] = _derive_audit_from_restored_box(
            driver, box, cap_meta,
        )
        return
    # ... else fall through to the synthetic harness chain ...
```

Two source-of-truth tiers:

1. **Artifact-preferred.** If
   `tests/otbox/captures/<capture_name>/snapshot.tar.gz` +
   `metadata.json` are committed, the checkpoint extracts the archive
   into the freshly-provisioned box, rewrites absolute paths the same
   way `snapshot.restore_snapshot` does, re-saves `meta.json` with the
   current box id, and re-derives the audit dict from on-disk
   evidence (`state.json` under `~/.opentraces/projects/<slug>/`, the
   project's git HEAD, the encoded transcript path). This is the
   higher-fidelity path, produced by a real logged-in `claude` (or
   `codex`/`hermes`) via the simulated-user pipeline below.
2. **Synthetic-fallback.** Otherwise the existing fake-claude harness
   chain runs end-to-end (deterministic, offline, default-CI safe):
   stages the harness onto `$HOME/bin/claude`, drives the
   `simple-refactor` (or per-checkpoint) corpus through the real
   opentraces `PreToolUse`/`PostToolUse` hooks, commits, fires the
   post-commit hook, ticks the watcher, runs `trail mature
   --commit HEAD`, rebuilds the Trace Index.

Audit shape is identical regardless of source — journey TOMLs
templating `{trace_id}` / `{commit_sha}` / `{step_index}` work
unchanged across both. Audits carry a `capture_metadata.source`
field (`"artifact"` or `"synthetic"`) so consumers can distinguish
provenance (plan 072 R4). Intermediate command outputs from the
synthetic chain (watcher-tick stdout, `trail mature` stdout) are
NOT preserved in artifact restores, so the artifact-restored audit
omits the numeric `tick_*` / `mature_*` fields. Journeys that depend
on those numbers must branch on `capture_metadata.source`.

To upgrade a checkpoint from synthetic to real-captured: run `make
capture-refresh SCENARIO=<name>` on a substrate with the real binary
on PATH (today: plan 073's Mac Mini work), commit the resulting
artifacts under `tests/otbox/captures/<name>/`, and the next checkpoint
resolution picks them up automatically.

## Simulated-user capture pipeline (plan 071)

The pipeline that produces those artifacts is a PTY/tmux runner driving
a real (or echo) agent binary against a scripted prompt sequence.

**Scenario TOML** (`tests/otbox/simulated_users/scenarios/<name>.toml`):

```toml
name = "add-helper-function"
description = "..."
agent = "claude"             # claude | codex | hermes | echo
binary_name = "claude"       # looked up on PATH; echo special-cased to _echo_binary.py

[initial_state]
template = "single-file-python-project"   # tests/otbox/simulated_users/templates/<name>/

[[turns]]
prompt = "Add a farewell helper to src/app.py"
expect_regex = "(?i)(I'll add|adding|let me)"
timeout_s = 60

[[turns]]
prompt = "yes"
expect_regex = "(?i)(committed|done|complete)"
timeout_s = 90

[capture]
artifact_dir = "add-helper-function"          # leaf under tests/otbox/captures/
expected_paths = ["src/app.py"]
```

`load_scenario(name)` validates the schema and resolves any
`[initial_state].template` to an absolute path under `templates/`.
`scenario_digest(scenario)` is the SHA-256 of the raw TOML bytes —
stamped into `metadata.json` so a capture artifact carries the exact
scenario revision it was produced from.

**PTY runner contract** (`tests/otbox/simulated_users/runner.py`):
`run_simulated_session(driver, box, binary, turns, *, initial_state_dir,
output_dir, env_extra=None) -> ScenarioResult`. Spawns the binary in
the box's project dir under tmux with `env <pinned-vars>` so the box's
isolated `HOME` / `opentraces_dir` / `fake_remote` survive any running
tmux server. Sends each `Turn.prompt`, polls `capture-pane` until
`Turn.expect_regex` matches or `Turn.timeout_s` fires, kills the
session in a `try/finally`. Always writes a `pane.log` for forensic
context, even on FAIL or SKIP. Verdicts:

- `PASS` — every turn's regex matched within its window.
- `FAIL` — turn timeout, regex miss, or unexpected runtime error;
  `turn_count` records how many turns succeeded BEFORE the failure.
- `SKIP` — clean abandon before any turn fired (missing binary,
  missing tmux). `turn_count` is 0. Callers decide whether SKIP is
  hard-fail or quiet exit.

**Echo meta-test binary** (`tests/otbox/simulated_users/_echo_binary.py`):
a synthetic stdin-driven REPL with canned responses for `"add a
farewell"` -> `"OK, I'll add the helper now"`, `"yes"` -> `"Done!
Committed."`, `"quit"`/`"exit"` -> goodbye. Supports `--version`. The
`echo-meta` scenario drives this binary through a 3-turn session to
exercise the runner in default CI without any real agent.

**`make capture-refresh SCENARIO=<name>` lifecycle**:

1. Load + validate the scenario; resolve the binary (echo special-case
   → in-tree path; otherwise `shutil.which(binary_name)`).
2. `--dry-run` (optional): print what would happen — binary path,
   turn count, base checkpoint, target artifact paths — and exit 0.
3. Binary missing → SKIP cleanly (exit 0). Default CI never depends on
   a real `claude` install.
4. `resolve_checkpoint(driver, "c-installed-source")` (or
   `--base-checkpoint <name>`) forks a fresh box from the cached
   snapshot.
5. Copy `[initial_state].template` contents into `box.project`.
6. `run_simulated_session(...)` drives the agent through the turns.
7. PASS → `create_snapshot` → copy archive into
   `tests/otbox/captures/<name>/snapshot.tar.gz`, write
   `metadata.json` (timestamp, scenario_digest, binary path/version,
   opentraces schema + CLI versions, base_checkpoint), tear box down.
8. FAIL → leave the box up for inspection (pane log at
   `box.logs/capture-refresh/<scenario>/pane.log`), exit 3.

**Artifact storage layout**:

```
tests/otbox/captures/<scenario>/
  snapshot.tar.gz       # full box tar archive (extractable into any fresh box)
  metadata.json         # captured_at, scenario_digest, agent + binary version,
                        # opentraces_schema_version, opentraces_cli_version,
                        # base_checkpoint, turn_count
```

These are committed verbatim under `tests/otbox/captures/`. If they
grow past ~1MB each, the plan is to migrate to Git LFS in a follow-up
(out of scope for plan 071).

## Tiered SSoT coverage gate (plan 069)

Every journey TOML declares a `tier_label` (default `bronze`). The
inventory's per-trajectory rollup section computes the max tier across
all journeys naming each trajectory:

| Tier | Meaning |
|---|---|
| `bronze` | smoke / `--help` shape / empty-state contract — proves the CLI parses + returns its documented envelope |
| `silver` | non-trivial real assertions, but not against credible captured world state |
| `gold` | credible-state coverage — fork from a captured-session checkpoint, exercise the real consumer-API contract against real evidence |

The strict gate lives in `tests/otbox/jtbd.py::AGENT_FACING_TRAJECTORIES_MIN_GOLD`:

```python
AGENT_FACING_TRAJECTORIES_MIN_GOLD: frozenset[str] = frozenset({
    "survival-walk",
    "automate-dataset-runs",
    "pr-lineage-publish",
    "inspect-security-pipeline",
})
```

Each entry MUST have at least one `tier_label = "gold"` journey
covering it; `make otbox-inventory --strict` (and `pytest
tests/otbox/test_jtbd_ssot.py`) fail loudly when any drop below the
required tier. `build-dataset-from-lineage` is also gold via the
`agent-session-trail-explain-happy` slice but is not in the gate set
because its gold coverage is structural rather than agent-facing.

Inspect coverage at a glance:

```bash
make otbox-inventory --strict
# ...
# - Trajectories covered: **31** (5 gold, 0 silver, 26 bronze)
```

Current state: **5/31 trajectories gold today** (`survival-walk`,
`automate-dataset-runs`, `pr-lineage-publish`,
`inspect-security-pipeline`, `build-dataset-from-lineage`); the
remaining 26 are bronze and the next phase of work targets them
incrementally.

## CI guard

otbox guards itself with multiple pytest suites:

```bash
make otbox-slice           # just the thin Tier 0 vertical slice (1 PASS)
make otbox-journeys        # full Tier 0 catalogue sweep + residue check (55 PASS)
make otbox-tier1           # full Tier 1 slice + catalogue (opt-in, OT_OTBOX_TIER1=1)
make otbox-matrix          # (journey × checkpoint) sweep
make otbox-inventory       # rebuild journey-inventory.md + plan 063/069 SSoT gates (strict)
make otbox-agent-session   # plan 064/068/072 substrate: 12 PASS + 1 SKIP
make capture-refresh SCENARIO=echo-meta   # plan 071 capture lifecycle (meta-test)
```

`make otbox-tier1` sets `OT_OTBOX_TIER1=1`. With `OT_OTBOX_SSH_TARGET`
already set, it runs against the operator's tailnet target; without
it, the pytest spins up a per-test local sshd on a high port (no
system "Remote Login" change needed) so the suite runs offline against
a same-machine SSH endpoint. Default `make otbox-journeys` does **not**
require a tailnet and never runs the Tier 1 suite.

`make otbox-agent-session` covers the plan-064/068/072 substrate:
`test_fake_harness.py` (5 shape tests on the Python harness),
`test_agent_session_slice.py` (4 captured-checkpoint pytests for the
plan-068 family + the slice end-to-end test),
`test_real_agent_optin.py` (3 contract tests for the opt-in real
`claude` lane — SKIPs cleanly without a real binary on PATH). 12 PASS
+ 1 SKIP in ~1.5s on a cached checkpoint.

## Tier 1 quick start

```bash
./otbox init                       # write .otbox.yaml + SKILL
export OT_OTBOX_TIER1=1
export OT_OTBOX_SSH_TARGET=user@host       # or a Tailscale host name
./otbox warmup                     # provision a remote box, park it
./otbox sync                       # rsync the working tree
./otbox seed smoke
./otbox snapshot t1-base           # tars on the remote, pulls archive back
./otbox down
./otbox up --from t1-base --driver remote   # restores into a fresh remote box
./otbox journey cli-publish-happy-path --artifacts
./otbox down
```

The Tier 0 ↔ Tier 1 archive interchange is a hard invariant: a
snapshot taken on a `local` box can be restored to a `remote` box and
vice versa (verified by `test_interchange_invariant_local_to_remote`).

## Autonomous delivery contract

A fresh agent can build and verify Tier 0 with **no network access**:
the `local` driver needs only the repo `.venv` (the documented dev
setup), seeds use committed fixtures only, journeys publish to the
fake HF remote, and the captured-session checkpoints run the synthetic
fallback unless real-agent artifacts have been committed under
`tests/otbox/captures/`. `make otbox-journeys` and `make
otbox-agent-session` together are the verification commands.

## Troubleshooting

- **A checkpoint won't resolve / stale audit.** Editing a checkpoint
  builder invalidates the cache; delete the offending snapshot at
  `.otbox/snapshots/_checkpoint-<name>-<hash>.tar.gz` and re-resolve.
  All descendants rebuild on the next run because the content-addressed
  hash recurses through `composed_from`.
- **`make capture-refresh` reports "binary not found".** The scenario
  named a real agent (e.g. `binary_name = "claude"`) that isn't on
  PATH. The CLI exits 0 with `status: "skipped"` — this is the
  contract, not a bug. Either install + log into the binary, or run
  the `echo-meta` scenario (uses the in-tree `_echo_binary.py`) to
  exercise the pipeline.
- **Journey assertions fail after a schema bump.** The captured-session
  audit drifted (e.g. an additional event the checkpoint now emits, a
  renamed envelope key). Re-resolve the offending checkpoint to
  re-derive the audit, then re-run the journey. If the assertion shape
  is what changed, update the journey TOML; the assertion vocabulary
  is documented in `journey.py::_eval_assertion`.
- **`tmux not installed` SKIP on a tmux step or the simulated-user
  runner.** Install tmux (`brew install tmux` / `apt install tmux`) on
  the host. The runner SKIPs cleanly rather than failing so default CI
  on minimal images stays green.
- **Precondition conflict SKIP.** A journey declared both
  `[preconditions]` and an explicit `from_checkpoints`, and the pinned
  checkpoint's `provides=...` doesn't satisfy the declared keys.
  Either weaken the preconditions, change the pin, or drop the
  explicit pin and let the resolver pick the first matching checkpoint.
