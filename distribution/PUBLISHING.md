# Publishing Guide

Two packages are published from this monorepo: `opentraces-schema` (dependency) and `opentraces` (CLI).
Authentication uses GitHub Actions trusted publishing (OIDC), no API tokens needed.

## Setup (already done)

Trusted publishers registered on PyPI and TestPyPI:

| Package | PyPI environment | TestPyPI environment |
|---------|-----------------|---------------------|
| `opentraces-schema` | `pypi-schema` | `testpypi-schema` |
| `opentraces` | `pypi-cli` | `testpypi-cli` |

Matching GitHub environments created in repo Settings > Environments.

## Version files

| Package | File | Field |
|---------|------|-------|
| `opentraces-schema` | `packages/opentraces-schema/src/opentraces_schema/version.py` | `SCHEMA_VERSION` |
| `opentraces` | `src/opentraces/__init__.py` | `__version__` |

## Publishing a release

### 1. Bump versions

Edit the version files above. If only the CLI changed, you can skip bumping the schema version.

### 2. Verify locally

```bash
source .venv/bin/activate
python -m pip install --upgrade build twine
python -m pytest -q

# Build and check both packages
cd packages/opentraces-schema && rm -rf dist && python -m build && python -m twine check dist/* && cd ../..
rm -rf dist && python -m build && python -m twine check dist/*
```

### 3. Test on TestPyPI (first time or when unsure)

Push your changes to `main`, then:

1. Go to **Actions** > **Publish** > **Run workflow**
2. Select `testpypi` and `both`
3. Wait for both jobs to pass
4. Verify:
   ```bash
   python -m venv /tmp/ot-test
   source /tmp/ot-test/bin/activate
   pip install --index-url https://test.pypi.org/simple/ --no-deps opentraces-schema==X.Y.Z
   pip install --index-url https://test.pypi.org/simple/ --no-deps opentraces==X.Y.Z
   pip install --index-url https://pypi.org/simple/ 'click>=8.0' 'huggingface_hub>=0.20.0' 'pydantic>=2.0' 'pyclack-cli>=0.4.0' 'requests>=2.31.0'
   opentraces --help
   ```

### 4. Release to PyPI

```bash
git add .
git commit -m "release: opentraces vX.Y.Z"
git tag -a vX.Y.Z -m "opentraces vX.Y.Z"
git push origin main --tags
```

Then create a **GitHub Release** for the tag `vX.Y.Z`. This automatically triggers the Publish workflow, which builds and publishes both packages to PyPI (schema first, then CLI).

### 5. Verify

```bash
python -m venv /tmp/ot-verify
source /tmp/ot-verify/bin/activate
pipx install opentraces==X.Y.Z
opentraces --help
```

## Manual publish (without a release)

Go to **Actions** > **Publish** > **Run workflow**, then pick:
- **repository**: `testpypi` or `pypi`
- **package**: `opentraces-schema`, `opentraces`, or `both`

## How it works

The workflow (`.github/workflows/publish.yml`) has 4 jobs:

1. **Build opentraces-schema** - builds the schema wheel
2. **Build opentraces** - builds the CLI wheel
3. **Publish opentraces-schema** - uploads schema (runs first)
4. **Publish opentraces** - uploads CLI (waits for schema to succeed)

On a GitHub Release, both packages publish to PyPI. On manual dispatch, you choose the target registry and which package(s).

## Publishing only the schema

If you made a breaking schema change and need to publish it before the CLI is ready:

1. Bump `SCHEMA_VERSION` in `packages/opentraces-schema/src/opentraces_schema/version.py`
2. Run workflow manually with `package=opentraces-schema`
3. Update `opentraces` dependency in `pyproject.toml` to match
