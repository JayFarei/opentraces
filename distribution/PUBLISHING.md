# Publishing Guide

This repository uses GitHub Actions + trusted publishing for releases.

Two packages are published: `opentraces-schema` (dependency) and `opentraces` (CLI).

## First-time setup (one-time)

1. Create accounts on [TestPyPI](https://test.pypi.org) and [PyPI](https://pypi.org).
2. In **each** registry, add a **Trusted Publisher** for **each package**:

   **opentraces-schema** (4 entries: 2 registries x 1 package):
   - Owner: `JayFarei`
   - Repository: `opentraces`
   - Workflow: `publish.yml`
   - Environment: `pypi-schema` (on PyPI) / `testpypi-schema` (on TestPyPI)

   **opentraces** (4 entries: 2 registries x 1 package):
   - Owner: `JayFarei`
   - Repository: `opentraces`
   - Workflow: `publish.yml`
   - Environment: `pypi-cli` (on PyPI) / `testpypi-cli` (on TestPyPI)

3. In GitHub repo **Settings > Environments**, create four environments: `pypi-schema`, `pypi-cli`, `testpypi-schema`, `testpypi-cli`.
4. Ensure Actions are enabled for this repository.

## Release workflow (recommended)

1. Bump versions:
   - Schema: `packages/opentraces-schema/src/opentraces_schema/version.py`
   - CLI: `src/opentraces/__init__.py`
2. Run checks locally:
   ```bash
   python -m pip install --upgrade build twine
   python -m pytest -q
   cd packages/opentraces-schema && python -m build && python -m twine check dist/* && cd ../..
   python -m build && python -m twine check dist/*
   ```
3. Commit, tag, and push:
   ```bash
   git add .
   git commit -m "release: opentraces vX.Y.Z"
   git tag -a vX.Y.Z -m "opentraces vX.Y.Z"
   git push origin main --tags
   ```
4. Create a GitHub Release for `vX.Y.Z`.
5. Confirm success in Actions:
   - Workflow: `Publish`
   - Jobs: `Publish opentraces-schema`, then `Publish opentraces`

## Manual publish runs

- TestPyPI: run workflow `Publish` with `repository=testpypi` and choose which package.
- PyPI: run workflow `Publish` with `repository=pypi` and choose which package.

## Verify install

From PyPI:

```bash
python -m venv /tmp/ot-verify
source /tmp/ot-verify/bin/activate
pip install opentraces==X.Y.Z
opentraces --help
```

From TestPyPI:

```bash
python -m venv /tmp/ot-test
source /tmp/ot-test/bin/activate
pip install --index-url https://test.pypi.org/simple/ --no-deps opentraces-schema==X.Y.Z
pip install --index-url https://test.pypi.org/simple/ --no-deps opentraces==X.Y.Z
pip install --index-url https://pypi.org/simple/ 'click>=8.0' 'huggingface_hub>=0.20.0' 'pydantic>=2.0' 'pyclack-cli>=0.4.0' 'requests>=2.31.0'
opentraces --help
```
