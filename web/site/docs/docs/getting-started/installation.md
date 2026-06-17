# Installation

## pipx

```bash
pipx install opentraces
```

## brew

```bash
brew install JayFarei/opentraces/opentraces
```

## skills.sh

```bash
npx skills add jayfarei/opentraces
```

Installs the opentraces skill via [skills.sh](https://skills.sh) so your coding agent can drive the init, review, and push workflow conversationally. `opentraces init` also installs the bundled skill into the current project.

## Copy to your agent

Paste this into your coding agent (Claude Code, Codex CLI, or Pi):

```
{{AGENT_PROMPT}}
```

The agent runs this as an interview: it asks you how to configure opentraces, one decision at a time, and waits for your answer before applying it. You choose the tracking mode (global auto-enroll vs manual per-project opt-in), which capture hooks to install, whether to install the shared skill, whether to authenticate with Hugging Face now, and any optional security passes. The prompt is tool-agnostic, so agents with a structured question UI render it as choices while others simply ask in chat. After hook or skill installation, start a fresh agent session before expecting the new capture hooks or skill to be available.

## From Source

```bash
git clone https://github.com/JayFarei/opentraces
cd opentraces
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
```

## Verify Installation

```bash
opentraces --version
opentraces --help
```

## System Requirements

| Platform | Status |
|----------|--------|
| macOS (ARM64, x86_64) | Supported |
| Linux (x86_64, ARM64) | Supported |
| Windows (WSL) | Supported via Linux binary |

Python 3.10 or later is required.

## Upgrading

From inside an initialized project, the preferred path is:

```bash
opentraces setup upgrade
```

This detects whether you installed via `pipx`, Homebrew, pip, or source, upgrades the CLI, re-renders every installed integration glue file (watcher shim, git post-commit hook, Claude Code and Codex CLI hooks, OTLP settings and autostart) re-stamped to the new version, and refreshes the project skill and hook. Use `opentraces setup upgrade --integrations-only` to re-render the installed glue without a CLI bump, or `--skill-only` to refresh just the skill and hook.

Outside a project context, upgrade with the package manager you originally used:

```bash
pipx upgrade opentraces
# or
brew upgrade JayFarei/opentraces/opentraces
# or
pip install --upgrade opentraces
```

## Uninstalling

`opentraces setup uninstall` is the symmetric inverse of `setup` — one command that reverses the whole multi-surface install (capture hooks, the OTLP receiver + its `~/.claude/settings.json` env keys, the watcher daemon, the skill, shell completions, per-repo git post-commit hooks, security-tool flags) and stops every opentraces process. It is data-safe by default — prefer it over `rm -rf ~/.opentraces`, which leaves hooks, daemons, git refs, and completions behind.

```bash
opentraces setup uninstall --dry-run   # recommended first run: prints the plan, changes nothing
opentraces setup uninstall             # default: reverse every install-time patch + daemon, PRESERVE all captured data
```

The default (`--integrations-only`) tier preserves every captured trace, dataset, bucket, and Git ref (`refs/opentraces/*`, `refs/notes/opentraces`); you can re-`setup` later and pick up where you left off. To also delete the captured data:

```bash
opentraces setup uninstall --purge     # ALSO delete captured data + git refs — UNRECOVERABLE (typed confirmation, or --yes)
```

`--purge` deletes the captured corpus (bucket, datasets, projects, staging) and the opentraces Git refs in one shot — both the canonical Trail event log and its only local replay source (the bucket) — so it requires a typed confirmation. A configured remote bucket is not deleted (local-only teardown) and is reported in the residue summary.

Finally, remove the package itself with the command for your install method (printed by `setup uninstall`):

```bash
pipx uninstall opentraces
# or
brew uninstall jayfarei/opentraces/opentraces
# or
pip uninstall opentraces
```

Your HuggingFace login (`~/.cache/huggingface/token`) is never touched by uninstall.
