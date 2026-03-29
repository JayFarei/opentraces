<!-- /autoplan restore point: /Users/jayfarei/.gstack/projects/JayFarei-opentraces/main-autoplan-restore-20260328-230814.md -->
---
title: "feat: CLI Simplification & Verb Consistency"
type: feat
status: reviewed
date: 2026-03-28
---

# CLI Simplification & Verb Consistency Plan (v2)

## Context

The CLI grew around three security tiers that confused users. The user's insight: there are really only two modes. Either you trust the scanner and auto-push (set it and forget it), or you want to review traces before they leave your machine (human in the loop). Everything else is implementation detail.

This rewrite collapses the CLI around that insight, makes `init` the single onboarding command, and uses Claude Code's `SessionEnd` hook to auto-parse sessions so users never run `opentraces parse` manually.

## Two Modes

```
AUTO MODE (set and forget):
  SessionEnd hook fires → parse → scan → redact → auto-commit → auto-push
  User never touches CLI again after init.

REVIEW MODE (human in the loop):
  SessionEnd hook fires → parse → scan → redact → stage locally
  User runs: opentraces review → opentraces commit → opentraces push
```

Both modes run the full secret scanner (18 regex patterns + Shannon entropy + path anonymization). The mode controls whether a human reviews the output, not whether security scanning happens.

## Target CLI Surface

```
opentraces init                         # one-stop-shop: auth + mode + remote + hook install
opentraces status                       # staged/committed/pushed counts, last capture time
opentraces review [--web|--tui]         # approve/reject traces (review mode only)
opentraces commit [-m "message"] [--all]# bundle approved traces into a commit group
opentraces push [--private|--public]    # upload committed traces to HF Hub
opentraces remote [set|remove]          # manage HF dataset remote
opentraces login [--token]              # standalone re-auth (also called by init if needed)
opentraces logout                       # remove credentials
opentraces parse [--limit N]            # power-user: manual catch-up parse
opentraces config show|set              # view/modify settings
opentraces assess [--judge]             # optional quality gate
```

The main flow for a new user: `opentraces init` (once) → everything else is automatic or `review → commit → push`.

---

## Changes

### 1. Remove DataClaw import

**Why:** Unused, adds confusion, no clear use case.

| File | Action |
|------|--------|
| `src/opentraces/parsers/dataclaw_import.py` | Delete |
| `src/opentraces/parsers/__init__.py` | Remove DataClawImporter registration + alias |
| `src/opentraces/parsers/base.py` | Remove "DataClaw JSONL" from FormatImporter docstring |
| `src/opentraces/cli.py` | Remove `import_traces` command, update `capabilities` and `introspect` |
| `tests/test_upload.py` | Remove `TestDataClawImport` class + `_make_dataclaw_record` helper |
| `tests/test_exporters.py` | Remove dataclaw protocol/registry/alias tests |

### 2. Two modes replace three tiers

**Schema change:** `security.tier` (1/2/3) → `security.mode` ("auto"/"review").

**Mapping:**
- Tier 1 (open) → auto mode (scan + redact + auto-push)
- Tier 2 (guarded) → review mode (scan + redact + classify + human review)
- Tier 3 (strict) → review mode (same pipeline, review is always required)

The classifier (`security/classifier.py`) runs in review mode to flag traces for attention. In auto mode it is skipped since no human will see the flags.

**Config change:** `.opentraces/config.json` key changes from `"tier": 2` to `"mode": "review"`.

**Backward compat:** `load_project_config()` reads old `tier` key and maps to mode: tier 1 → auto, tier 2/3 → review. One-time migration writes new format.

### 3. Init as one-stop-shop

The `init` command becomes the entire onboarding flow:

```python
@main.command()
@click.option("--mode", type=click.Choice(["auto", "review"]), default=None)
@click.option("--remote", type=str, default=None)
@click.option("--no-hook", is_flag=True, help="Skip hook installation")
def init(mode, remote, no_hook):
```

**Interactive flow (pyclack):**

```
┌  opentraces init
│
◇  Checking authentication...
│  ✓ Authenticated as jayfarei (or: launches login flow)
│
◆  How should traces be shared?
│  ● Auto — scan, redact, push automatically after each session
│  ○ Review — I review and approve traces before pushing
│
◆  Where should traces go?
│  ● jayfarei/opentraces (12 traces, 2 shards)
│  ○ jayfarei/my-traces (3 traces, 1 shard)
│  ○ Create new dataset...
│
◆  Install Claude Code hook?
│  Parses sessions locally after each coding session.
│  ● Yes, install (Recommended)
│  ○ No, I'll parse manually
│
◆  Confirm: auto mode → jayfarei/opentraces, hook installed?
│  Yes / No
│
└  Done! Your next Claude Code session will be captured automatically.
```

**Non-interactive (agent):** `opentraces init --mode auto --remote user/repo` skips all prompts. Add `--no-hook` to skip hook installation.

**Auth integration:** Init checks `HfApi.whoami()`. If not authenticated, runs the login flow inline (OAuth device code or --token paste). No separate `login` command needed for first-time users.

**Edge cases:**
- Already initialized: show current config, offer to reconfigure
- HF Hub unreachable: skip remote selection, user sets later via `opentraces remote set`
- Not using Claude Code: skip hook step, explain manual `opentraces parse`
- .claude/settings.json doesn't exist: create it with just the hook
- .claude/settings.json has existing hooks: merge, don't overwrite

### 4. SessionEnd hook installation

Init writes to `.claude/settings.json` (project-level):

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "opentraces _capture --session-dir \"$CLAUDE_SESSION_DIR\" --project-dir \"$PWD\"",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

**Hook behavior:**
- Fires after each Claude Code session ends
- Runs `_capture` which does the full pipeline: parse → enrich → scan → redact → anonymize → stage
- In auto mode: `_capture` also auto-commits and auto-pushes (new flag: `--auto-push`)
- In review mode: `_capture` stages only, user reviews later

**Breadcrumb fallback:** If the hook fails (timeout, crash), `_capture` writes session path to `.opentraces/pending-sessions.txt`. Next time any `opentraces` command runs, it checks this file and catches up. Makes the hook best-effort, not load-bearing.

### 5. Add `opentraces commit` CLI command

```python
@main.command()
@click.option("-m", "--message", type=str, default=None, help="Commit message")
@click.option("--all", "commit_all", is_flag=True, help="Commit all approved traces")
def commit(message, commit_all):
```

**Behavior:**
- Without `--all`: shows approved traces, lets user confirm which to include
- With `--all`: commits all APPROVED traces
- Without `-m`: auto-generates message from task descriptions (join first 3, truncate)
- Calls `StateManager.create_commit_group(trace_ids, message)` which also transitions each trace from APPROVED → COMMITTED
- Emits JSON with `commit_id`, `session_count`, `next_command: "opentraces push"`

**Agent mode:** `opentraces commit --all -m "message"` requires no prompts.

**State machine fix:** `create_commit_group()` must be updated to:
1. Accept `trace_ids` (not session_ids) for consistency with state machine keying
2. Call `set_trace_status(trace_id, COMMITTED)` for each trace in the group
3. The viewer's `/api/commit` endpoint must also be updated to use trace_ids

### 6. Redesign `opentraces remote`

Click group with subcommands:

```
opentraces remote              # show current remote
opentraces remote set [<repo>] # interactive selector OR direct set
opentraces remote remove       # remove remote from config
```

**`remote set` without argument (interactive):**
1. Calls `HFUploader.list_opentraces_datasets(username)` (new method using `api.list_datasets(filter="opentraces", author=username)`)
2. pyclack select: existing datasets + "Create new dataset..."
3. Writes to `.opentraces/config.json`

**`remote set <repo>` (direct):** Validates `owner/dataset` format (must contain exactly one `/`). Writes immediately.

**Always pair `author=username` with tag filter** to prevent showing other users' datasets.

### 7. Push respects mode

**Review mode:** `push` only uploads COMMITTED traces. If approved-but-uncommitted traces exist, prints hint: "N approved traces. Run `opentraces commit` first."

**Auto mode:** `push` is not typically called by users (auto-push handles it). But if called manually, it uploads any COMMITTED or auto-committed traces.

**Changes to push:**
- Remove `--approved-only` flag (commit step replaces this)
- Add `get_committed_traces()` to StateManager (queries COMMITTED status)
- Push calls `get_committed_traces()` instead of `get_pending_upload_traces()`

### 8. Unify config format → JSON

Per-project config migrates from `config.yml` to `config.json`.

**Migration in `load_project_config()`:**
1. Check for `config.json` first
2. If not found, check for `config.yml`
3. If YAML found: read it, write `config.json`, rename `config.yml` → `config.yml.bak`
4. If neither: return default `{"mode": "review"}`

**`init` guard:** Check for EITHER `config.json` OR `config.yml` to detect already-initialized projects.

**Atomic write:** Write to `config.json.tmp`, then `os.replace()` to `config.json`.

**Clean up dead code:**
- Remove `dataset_name_template` field from `Config` model (line 42 of config.py)
- Remove `get_dataset_name()` function (lines 178-180 of config.py)
- Wire up existing `ProjectConfig` Pydantic model for validation

### 9. Fix `_capture` state bug

**Bug:** `_capture` lines 542-553 do raw JSON read/write to `.opentraces/state.json`, bypassing `StateManager`. This will clobber `commit_groups`.

**Fix:** Remove the raw JSON block. Use `StateManager` exclusively for all state mutations.

### 10. pyclack integration

**Hard dependency** in `pyproject.toml`: `pyclack-cli[prompts]`

**Lazy import** in interactive code paths only:
```python
def _interactive_init():
    from pyclack.prompts import select, text, confirm
    # ...
```

This prevents import-time crashes in CI/headless/broken-install scenarios.

---

## Files to modify

| File | Changes |
|------|---------|
| `src/opentraces/cli.py` | Rewrite `init` (auth+mode+remote+hook), add `commit` cmd, redesign `remote` as group, update `push` to use COMMITTED, update `_capture` for --auto-push flag, remove `import_traces`, remove _capture raw JSON bug |
| `src/opentraces/config.py` | Remove `dataset_name_template` + `get_dataset_name()`, migrate per-project YAML→JSON, add mode field, wire ProjectConfig Pydantic model, backward-compat tier→mode mapping |
| `src/opentraces/state.py` | Update `create_commit_group()` to accept trace_ids + transition status, add `get_committed_traces()` method |
| `src/opentraces/upload/hf_hub.py` | Add `list_opentraces_datasets(username)` method |
| `src/opentraces/parsers/dataclaw_import.py` | Delete |
| `src/opentraces/parsers/__init__.py` | Remove dataclaw registration |
| `src/opentraces/parsers/base.py` | Update docstring |
| `src/opentraces/review/web/app.py` | Update `/api/commit` to use trace_ids |
| `pyproject.toml` | Add `pyclack-cli[prompts]` as hard dependency |
| `tests/test_upload.py` | Remove dataclaw tests |
| `tests/test_exporters.py` | Remove dataclaw tests |
| `tests/test_e2e_flow.py` | Rewrite for modes, config.json, commit step |
| `tests/` (new files) | test_commit.py, test_push_modes.py, test_remote.py, test_config_migration.py, test_hooks.py |

## Verification

1. `opentraces init` — pyclack: auth check, mode select, remote selector, hook install, confirm
2. `opentraces init --mode auto --remote user/repo` — non-interactive, no prompts
3. `.claude/settings.json` contains SessionEnd hook after init
4. SessionEnd hook fires and runs `_capture` (test with a real Claude Code session)
5. `opentraces remote set` — interactive selector with existing opentraces datasets
6. `opentraces remote set user/repo` — direct set, validates format
7. `opentraces commit --all -m "test"` — transitions APPROVED → COMMITTED
8. `opentraces push` in review mode — only uploads COMMITTED traces
9. `opentraces push` in review mode with uncommitted traces — shows hint
10. `_capture --auto-push` in auto mode — parses + pushes in one shot
11. `opentraces import` — command no longer exists
12. Old `.opentraces/config.yml` auto-migrates to `config.json`
13. Old `tier: 2` config maps to `mode: review`
14. Breadcrumb: if hook fails, `.opentraces/pending-sessions.txt` written, next CLI command catches up
15. `pytest tests/ -v` — all tests pass

## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale |
|---|-------|----------|---------------|-----------|-----------|
| 1 | CEO | Rewrite plan to match 2-mode premises | Mechanical | P6 action | Plan was stale, premises confirmed |
| 2 | CEO | Keep `commit` verb despite subagent challenge | Taste | User sovereignty | User explicitly confirmed review→commit→push flow |
| 3 | CEO | Add breadcrumb fallback for hook failure | Mechanical | P1 completeness | <20 LOC, covers the main risk |
| 4 | CEO | Defer multi-agent support | Mechanical | P3 pragmatic | Strategic but out of scope |
| 5 | CEO | Keep pyclack as hard dep | Mechanical | User sovereignty | User chose this explicitly |
| 6 | Design | Add offline fallback for remote selection | Mechanical | P1 completeness | Edge case, in blast radius |
| 7 | Design | Add non-Claude-Code path for hook step | Mechanical | P1 completeness | Edge case, in blast radius |
| 8 | Eng | Fix create_commit_group to transition status | Mechanical | P5 explicit | Pre-existing bug, will cause silent failures |
| 9 | Eng | Fix push to read COMMITTED not APPROVED | Mechanical | P5 explicit | Required for mode-based push |
| 10 | Eng | Fix _capture raw JSON clobber | Mechanical | P5 explicit | Pre-existing bug, conflicts with commit groups |
| 11 | Eng | Change commit_group to accept trace_ids | Mechanical | P4 DRY | Matches state machine keying |
| 12 | Eng | Lazy-import pyclack | Mechanical | P3 pragmatic | Prevents crashes in headless envs |
| 13 | Eng | init guard checks both config filenames | Mechanical | P5 explicit | Prevents silent config overwrite |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | issues_resolved | 8 findings, all resolved |
| Design Review | `/plan-design-review` | Terminal UI | 1 | clean | 2 findings, both in-scope |
| Eng Review | `/plan-eng-review` | Architecture & tests | 1 | issues_resolved | 14 findings, all addressed |
| CEO Voices | autoplan | Independent challenge | 1 | subagent-only | Codex truncated, 4/6 flagged |
| Eng Voices | autoplan | Independent challenge | 1 | subagent-only | Codex truncated, 3/6 flagged |

**VERDICT:** REVIEWED. 1 taste decision for final gate (commit verb). Plan rewritten to match confirmed premises.
