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

```bash
pipx uninstall opentraces
# or
brew uninstall opentraces
# or
pip uninstall opentraces
```

To also remove local data and credentials:

```bash
rm -rf ~/.opentraces
```
