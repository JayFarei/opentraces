# Installation

## pipx

```bash
pipx install opentraces
```

## brew

```bash
brew install JayFarei/opentraces/opentraces
```

## Copy to your agent

Paste this into your coding agent (Claude Code, Cursor, Codex, etc.):

```
set up opentraces for this project
```

The agent will install the CLI, authenticate, create a private HF dataset, and install the session hook. No manual setup required.

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
