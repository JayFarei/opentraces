# otbox — snapshottable full test environment

otbox seeds a fully-populated opentraces world (initialized project,
replayed traces, real git repo, built Trace Index, bound fake HF
remote), **snapshots** it, and lets any product user journey be torn
down and restarted in seconds. It is a dev/CI tool — *not* a shipped
product surface, and intentionally absent from `opentraces --help`.

Governing specs: `kb/plans/060-otbox-test-environment.md` (Tier 0,
delivered), `kb/plans/061-otbox-tailscale-local-tier1.md` (Tier 1 over
SSH/Tailscale, delivered), and `kb/plans/062-otbox-checkpoint-journey-matrix.md`
(journey × checkpoint matrix, delivered). Lifecycle vocabulary borrowed
from [crabbox](https://crabbox.sh), opentraces-specific.

Bootstrap a new repo with `./otbox init` — writes `.otbox.yaml` defaults
and the agent skill at `.agents/skills/otbox/SKILL.md`.

## Checkpoint × journey matrix (plan 062)

Every journey TOML declares ``from_checkpoints = [...]`` naming the
resumable starting state(s) it runs from. The matrix runner builds each
checkpoint once per run, snapshot-forks it for every dependent journey,
and emits a coverage row.

```bash
./otbox matrix                                # run the whole matrix
./otbox matrix --journey 'install*'           # filter by name
./otbox matrix --checkpoint 'c-installed-*'   # filter by base checkpoint
./otbox matrix --lane core                    # only core-lane journeys
./otbox matrix --inventory                    # rebuild Click × journey map
```

Bundled checkpoints today:

| Name | Built by | What it adds |
|---|---|---|
| `c-empty` | builder | a bare provisioned box, nothing installed |
| `c-prereqs-present` | composed_from c-empty | + verified python3.10+, git, rsync |
| `c-installed-source` | composed_from c-prereqs-present | + opentraces installed editable in `box.project/.testvenv` |

### Captured-session checkpoints, artifact-preferred, synthetic-fallback

The captured-session checkpoint family (`c-captured-real-session`,
`c-captured-with-revert`, `c-captured-multi-skill`,
`c-captured-with-pr-branch`) has two source-of-truth tiers (plan 072):

1. **Artifact-preferred.** If `tests/otbox/captures/<name>/snapshot.tar.gz`
   + `metadata.json` are committed, the checkpoint restores that
   pre-captured snapshot in-place (higher-fidelity, real-agent driven
   via plan 071's `make capture-refresh SCENARIO=<name>` on the Mac
   Mini runner, plan 073).
2. **Synthetic-fallback.** Otherwise the existing fake-claude harness
   chain runs (deterministic, offline, default-CI safe).

Audit shape is identical regardless of source — journey TOMLs
templating `{trace_id}` / `{commit_sha}` / `{step_index}` work
unchanged across both. Audits carry a `capture_metadata.source`
field (`"artifact"` or `"synthetic"`) so consumers can distinguish
provenance and plan 074's drift detector can flag stale captures.

Snapshots cache content-addressed at `.otbox/snapshots/_checkpoint-<name>-<hash>.tar.gz`
(gitignored). Editing a checkpoint builder invalidates the cache for
that checkpoint and all descendants.

Inventory: `make otbox-inventory` regenerates
`tests/otbox/catalogue/journey-inventory.md`, the Click registry ×
journey-ownership map. The plan-045 / plan-062 coverage gate consumes
this file.

## Quick start

```bash
./otbox up --seed smoke                 # provision a box + seed a world
./otbox snapshot smoke-base             # freeze it to a tar archive
./otbox down                            # tear the box down (zero residue)
./otbox up --from smoke-base            # restore a fresh box, fast
./otbox journey cli-publish-happy-path  # run a real CLI journey -> PASS
./otbox artifacts                       # bundle run evidence for a PR
./otbox down --all                      # clean up
```

Every command takes `--json` for stable machine-readable output (so an
agent can drive otbox without reading source).

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
| `artifacts [--box ID] [--label L]` | bundle journey-run evidence for a PR |
| `status [--box ID]` / `list` | inspect boxes, snapshots, drivers, seeds, journeys |
| `snapshot-rm <name>` | delete a snapshot |
| `image build [--tag T]` | build the Linux runtime image for the `docker` driver |

## Architecture

```
otbox  (repo-root shim, like otd)
  -> python -m tests.otbox
       cli.py        argparse dispatch, --json
       env.py        box layout, isolated env, CLI-entrypoint resolution
       drivers/      substrate behind one Driver protocol
         local.py    DEFAULT Tier 0 — HOME-isolated filesystem sandbox
         docker.py   opt-in Tier 0 — containerized (needs `otbox image build`)
         remote.py   opt-in Tier 1 — SSH-lease stub, gated by OT_OTBOX_TIER1
       seed.py       seeded-world builders (smoke, world)
       snapshot.py   portable workspace-archive snapshot/restore
       journey.py    declarative TOML journey runner
       artifacts.py  PR-ready evidence bundles
       catalogue/journeys/*.toml   the journey catalogue
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

## Journeys

A journey is a declarative TOML scenario document under
`catalogue/journeys/`. The runner is generic — **add coverage by adding
a `.toml` file, not by editing harness code.** The schema extends the
plan-045 release-UAT format with `tier` and `seed`:

```toml
name = "..."
description = "..."
lane = "core"          # core | extended | diagnostic
tier = 0               # 0 = local/docker, 1 = remote lease
seed = "smoke"         # which seed scenario this journey expects
requires = ["cli", "git", "tmux", "tier1", "real_repl"]

[[steps]]
type = "cli"           # cli | shell | write_file | service | http_get | tmux
id = "status"
argv = ["--json", "status"]

[[assertions]]
kind = "stdout_contains"   # returncode | stdout_contains | stderr_contains
step = "status"            #   stdout_json | path_exists | file_count_min
value = "initialized"
```

Step path templating: `{project}`, `{home}`, `{fake_remote}`,
`{state_dir}`, `{opentraces_dir}`, `{box_root}`, `{box_id}`,
`{repo_root}`, `{port}`.

Current catalogue:

| Journey | Lane | Tier | Surface |
|---|---|---|---|
| `cli-publish-happy-path` | core | 0 | CLI: init → dataset → publish to fake remote |
| `cli-lifecycle` | core | 0 | CLI: status / config / trace get / remove |
| `trace-map-and-slice` | core | 0 | CLI: trace index / query / map / slice |
| `trail-blame-and-graph` | core | 0 | CLI: backfill / trail blame / trail graph |
| `bucket-inspect` | core | 0 | CLI: bucket status / manifest |
| `doctor-health` | extended | 0 | CLI: doctor JSON health |
| `web-viewer-smoke` | extended | 0 | Web: Flask review backend, headless |
| `tui-review-smoke` | extended | 0 | TUI: lazytraces in tmux |
| `web-products-stub` | diagnostic | 0 | marketing site + metrics-worker (owned, shallow) |
| `install-smoke-tier1` | extended | 1 | cross-OS install smoke (opt-in) |

## CI guard

otbox guards itself with two pytest suites:

```bash
make otbox-slice       # just the thin Tier 0 vertical slice
make otbox-journeys    # full Tier 0 catalogue sweep + residue check
make otbox-tier1       # full Tier 1 slice + catalogue (opt-in)
```

`make otbox-tier1` sets `OT_OTBOX_TIER1=1`. With
`OT_OTBOX_SSH_TARGET` already set, it runs against the operator's
tailnet target; without it, the pytest spins up a per-test local sshd
on a high port (no system "Remote Login" change needed) so the suite
runs offline against a same-machine SSH endpoint. Default `make
otbox-journeys` does **not** require a tailnet and never runs the
Tier 1 suite.

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
setup), seeds use committed fixtures only, and journeys publish to the
fake HF remote. `make otbox-journeys` is the verification command.
