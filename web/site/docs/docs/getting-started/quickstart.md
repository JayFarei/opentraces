# Quick Start

From local capture to a published Hugging Face shard.

## 1. Install

```bash
pipx install opentraces
```

## 2. Set Up

```bash
opentraces setup
```

`setup` is the machine-wide wizard. It walks each integration with one prompt, defaults in brackets:

- **claude-code, git, skill** capture hooks [yes] — Stop/PostCompact hooks, post-commit correlator, and the Claude Code skill.
- **watcher** [yes] — background incremental backfill after each commit, powers `opentraces blame`.
- **entity-parser (sem)** [yes] — entity-level diffs for richer commit attribution.
- **HuggingFace login** [yes] — device-code flow, needed before you can push. You can defer and run `opentraces auth login` later.
- **trufflehog** (Tier 1.5) [no] — global secret-scanner toggle; findings redact in place and force review.
- **llm-review** (Tier 2) [no] — global toggle for third-party LLM review; configure provider via `opentraces setup llm-review`.

Per-project review policy and remote are not set here, they live in `opentraces init`.

## 3. Initialize the Project

```bash
opentraces init
```

`init` wires the current repo into opentraces and prompts you for:

- **Agents** to connect (e.g. `claude-code`).
- **Review policy** — `review` every trace in the inbox, or `auto` (capture, sanitize, stage, push without review).
- **HuggingFace login**, if you skipped it during setup.
- **Remote dataset** — pick an existing `owner/repo` from your HuggingFace datasets, **create a new one** (init actually calls `create_repo` on the spot so you catch namespace errors now, not at first push), or skip for later. When creating, you also choose **visibility** (private by default).
- **Import existing traces** — if this repo already has Claude Code sessions, `init` asks whether to import them now or start fresh.

It also writes the committable marker at `.opentraces.json`, registers machine-local storage under `~/.opentraces/projects/<slug>/`, and installs the per-repo capture hook unless you pass `--no-hook`.

## 4. Inspect the Inbox

### Web inbox

```bash
opentraces web
```

The browser inbox shows each trace with timeline, review, and push flows. It is the richest surface for manual review and redaction.

![Web inbox - review view](/docs/assets/web-review.png)

![Web inbox - graph view](/docs/assets/web-graph.png)

### Terminal inbox

```bash
opentraces tui
```

The TUI is faster for shell-first review. It loads the same local inbox and exposes staging, rejection, discard, security details, and push.

![Terminal inbox](/docs/assets/tui.png)

CLI review is available too:

```bash
opentraces status
opentraces list --stage inbox
opentraces show <trace-id>
opentraces redact <trace-id>
```

## 5. Stage Traces For Upload

```bash
opentraces add --all
```

`add` moves Inbox traces into the visible `staged` set. `blocked` and `rejected` traces are refused until you fix or explicitly reject them.

## 6. Push

```bash
opentraces push
```

`push` uploads staged traces to the active remote as a new JSONL shard and refreshes the dataset card. By default it also runs quality scoring unless you pass `--no-assess`.

## What Happens Next

Your traces are available as a Hugging Face dataset:

```python
from datasets import load_dataset

ds = load_dataset("your-name/opentraces")
```

## Next Steps

- [Inbox & Review](/docs/workflow/review) - Web, TUI, and CLI review flows
- [Push](/docs/workflow/pushing) - Remotes, visibility, migrations, and gates
- [Security Tiers](/docs/security/tiers) - Review policy and layered scanning
- [CLI Reference](/docs/cli/commands) - Full 0.4 command surface
