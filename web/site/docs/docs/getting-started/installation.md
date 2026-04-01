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

Installs the opentraces skill via [skills.sh](https://skills.sh) so your coding agent can drive the full workflow (init, review, push) conversationally. Works with Claude Code, Cursor, Codex, and any agent that supports skills. `opentraces init` also auto-installs the skill when you initialize a project.

## Copy to your agent

Paste this into your coding agent (Claude Code, Cursor, Codex, etc.):

```
{{AGENT_PROMPT}}
```

The agent installs the CLI, authenticates, and initializes. `init` handles the skill installation automatically. After that the agent uses the skill file for everything else.

## From Source

```bash
git clone https://github.com/jayfarei/opentraces
cd opentraces
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
```

## Verify Installation

```bash
opentraces --version
```

## System Requirements

| Platform | Status |
|----------|--------|
| macOS (ARM64, x86_64) | Supported |
| Linux (x86_64, ARM64) | Supported |
| Windows (WSL) | Supported via Linux binary |

Python 3.10 or later is required.

## Upgrading

The preferred in-project upgrade path is:

```bash
opentraces upgrade
```

Auto-detects whether you installed via pipx, brew, or pip and upgrades accordingly. Also refreshes the skill file and session hook in the current project.

If you are outside a project context, use the direct package manager command instead:

```bash
pip install --upgrade opentraces
```

## Uninstalling

```bash
pip uninstall opentraces
```

To also remove local data and credentials:

```bash
rm -rf ~/.opentraces
```
