# Installation

Install the opentraces CLI via pip.

## pip

```bash
pip install opentraces
```

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
