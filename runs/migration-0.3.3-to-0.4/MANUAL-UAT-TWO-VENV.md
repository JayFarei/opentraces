# Manual UAT: real two-venv 0.3.3 -> 0.4 upgrade walk

This is the runnable script for the upgrade-UAT parts that a synthetic otbox
capture cannot stand in for: the actual binary swap, the shipped-CLI first-run
behavior, real-agent context capture, and live HuggingFace egress. Each step is
copy-pasteable. Steps marked **[auto]** are already automated as pytest/journey
coverage (pointer given); steps marked **[human]** need a live agent session or a
real network push and must be run by a person.

Covers the catalogue's real-v033-venv / manual-uat cases: U-setup-1, U-setup-7,
U-config-6, U-auth-1/2/3, U-bucket-2/3 (egress), U-ctx-3/4, U-ds-4, U-hf-2,
U-bucket-14, U-config-7.

## 0. Prerequisites (verified present 2026-05-29)

```bash
# Real 0.3.3 CLI in an isolated venv (rebuildable per HANDOFF if absent):
V033=/tmp/ot-v033-worktree/.venv-v033/bin/opentraces
$V033 --version          # -> opentraces, version 0.3.3

# Real 0.4 CLI = this repo's venv:
V04=.venv/bin/opentraces
$V04 --version           # -> opentraces, version 0.4.0

# Live-HF token for the publish leg:
ls ~/.opentraces/otbox-live-hf-token   # 38-byte token file
```

Use a throwaway HOME so the walk never touches your real `~/.opentraces`:

```bash
export UAT=$(mktemp -d /tmp/ot-uat-XXXX)
export HOME_SAVED=$HOME
export HOME=$UAT/home && mkdir -p $HOME $UAT/proj
cd $UAT/proj && git init -q && git config user.email u@uat.test && git config user.name UAT
# ... run the steps below ... then restore:  export HOME=$HOME_SAVED
```

## 1. Capture on 0.3.3, then swap to 0.4 (U-config-6, U-auth-1)  [auto + human]

**[auto]** The binary-to-binary read handoff (0.3.3 writes the home, 0.4 reads it
without crashing, config_version preserved) is automated:
`tests/test_migration_upgrade_uat.py::test_u_config_6_real_v033_home_is_read_by_real_v04`.

**[human]** Drive a real agent session under 0.3.3 to produce a genuine 0.3.0
trace with an `outcome.patch`, then read it on 0.4:

```bash
$V033 init --agent claude-code
# ... run a real Claude Code session that edits a file and ends ...
$V04 trace index rebuild
$V04 --json trace get <trace_id> | python -m json.tool | grep -A3 patches
#   EXPECT: patches[] non-empty (reconstructed) + metadata.legacy.patch present.
#   This is the P0 read-path fix proven on a REAL 0.3.3-captured trace.
```

## 2. Auth reuse (U-auth-1/2/3)  [human]

```bash
$V033 auth login --token $(cat ~/.opentraces/otbox-live-hf-token)
$V04 auth whoami --json          # EXPECT rc=0, username resolved, NO re-login
HF_TOKEN=hf_env_override $V04 auth whoami --json   # EXPECT env beats stored
```

## 3. No-surprise-egress + two-store separation (U-bucket-2/3)  [auto + human]

**[auto]** The egress-is-opt-in gate (storage=local / remote.enabled=false by
default; capture never auto-pushes) and the dataset-vs-bucket distinct-binding
invariant are automated: `test_u_bucket_2_egress_is_opt_in_not_token_gated` and
`test_u_bucket_3_dataset_and_bucket_remotes_are_distinct_bindings`.

**[human]** Confirm with a real capture + a network sniffer that no HF call fires
until `setup bucket` + `bucket remote push`:

```bash
# With the 0.3.3 token already stored but NO `setup bucket`:
# (run a 0.4 capture offline / with egress trapped) -> assert no HF request.
$V04 setup bucket --no-autostart
$V04 bucket remote push           # egress happens ONLY now, ONLY to the bucket repo
```

## 4. setup upgrade idempotency (U-setup-1)  [human]

```bash
# After a 0.3.3 install with old skill/hook files:
$V04 setup upgrade                # EXPECT: skill + hooks refreshed to 0.4 shape once
$V04 setup upgrade                # EXPECT: no-op (idempotent); legacy traces byte-intact
```

## 5. Real OTLP / Context Tree capture (U-ctx-3/4, U-setup-7)  [human, BLOCKED for auto]

```bash
$V04 setup capture-otlp --no-autostart   # patches ~/.claude/settings.json (12 OTel vars)
$V04 capture-otlp start
# ... run a REAL Claude Code session inside $UAT/proj ...
$V04 capture-otlp flush --session <id> --project $UAT/proj --trace-id <id>
$V04 ctx tree <new_trace_id> --json      # EXPECT: nodes present, capture_method=otel
# Bypass-safety: with the receiver DOWN, a real session must still complete
# normally (traffic to Anthropic never blocked).
```

This step cannot be automated in default CI: it needs the real `claude` binary
driving a session through the local OTLP receiver. See the BLOCKED note in
`log.md` and `kb/projects/opentraces/otel-capture.md`.

## 6. Full headless spine to a published 0.6.0 dataset (U-ds-4, U-hf-2)  [human]

```bash
$V04 workflow create ds1wf --template skill-command-trajectory-eval-v1
$V04 dataset new ds1 --workflow $HOME/.opentraces/workflows/ds1wf
$V04 dataset run ds1 --executor headless --json
$V04 dataset review approve ds1 --all
$V04 dataset remote create ds1 <owner>/<repo> --private
$V04 dataset publish ds1          # live-HF lane; rows must be schema 0.6.0 + patches[]
```

NOTE (pinned by `test_u_hf_1_dataset_publish_does_not_reach_hfuploader`): the live
`dataset publish` path uploads via `HfApi` directly and does NOT route through
`HFUploader`'s schema-ahead / migrate_outdated_shards guards. Forward HF shard
migration on a real upgrade is an unverified PRODUCT GAP; wiring it is tracked
separately, not part of this UAT.

## 7. Decommissioned surfaces (U-config-7, U-bucket-14, U-ds-3)  [human]

```bash
$V04 pull                         # EXPECT rc!=0 (no such command); NOTE: no replacement hint yet
$V04 dataset review ds1 --web     # EXPECT a clean decommission notice, not a traceback
$V04 dataset review ds1 --tui     # EXPECT a clean decommission notice
```

## Teardown

```bash
export HOME=$HOME_SAVED && rm -rf "$UAT"
```
