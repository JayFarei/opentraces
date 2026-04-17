# Quick Start

From local capture to a published Hugging Face shard.

## 1. Install

```bash
pipx install opentraces
```

## 2. Authenticate

```bash
opentraces auth login
```

Use `opentraces auth login --token` or `HF_TOKEN` if you are running headless.

## 3. Initialize the Project

```bash
opentraces init --review-policy review
```

`init` wires the current repo into opentraces:

- writes the committable marker at `.opentraces.json`
- registers machine-local storage under `~/.opentraces/projects/<slug>/`
- installs the capture hook unless you pass `--no-hook`
- optionally connects a Hugging Face remote

If this repo already has Claude Code traces, you can backfill them immediately:

```bash
opentraces init --import-existing
```

## 4. Inspect the Inbox

### Web inbox

```bash
opentraces web
```

The browser inbox shows each trace with timeline, review, and push flows. It is the richest surface for manual review and redaction.

![Web inbox - timeline view](/docs/assets/web-timeline.png)

![Web inbox - review view](/docs/assets/web-review.png)

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
- [CLI Reference](/docs/cli/commands) - Full 0.3 command surface
