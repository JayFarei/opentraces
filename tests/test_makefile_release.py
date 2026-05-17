from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLEAN = "rm -rf dist/ build/ packages/opentraces-schema/dist/ packages/opentraces-schema/build/"
SCHEMA_BUILD = "cd packages/opentraces-schema && python3 -m build"
VIEWER_BUILD = "cd web/viewer && npm install && npm run build"
CLI_BUILD = "python3 -m build"
SCHEMA_UPLOAD = "python3 -m twine upload packages/opentraces-schema/dist/*"
CLI_UPLOAD = "python3 -m twine upload dist/*"


def _make_dry_run(*targets: str) -> list[str]:
    result = subprocess.run(
        ["make", "-n", *targets],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def test_release_cleans_once_before_builds_and_uploads_built_artifacts() -> None:
    lines = _make_dry_run("release")

    assert lines.count(CLEAN) == 1
    assert lines.count(SCHEMA_BUILD) == 1
    assert lines.count(CLI_BUILD) == 1
    assert lines.index(CLEAN) < lines.index(SCHEMA_BUILD)
    assert lines.index(SCHEMA_BUILD) < lines.index(VIEWER_BUILD)
    assert lines.index(VIEWER_BUILD) < lines.index(CLI_BUILD)
    assert lines.index(CLI_BUILD) < lines.index(SCHEMA_UPLOAD)
    assert lines.index(SCHEMA_UPLOAD) < lines.index(CLI_UPLOAD)


def test_publish_targets_upload_existing_artifacts_without_rebuilding() -> None:
    lines = _make_dry_run("publish-schema", "publish-cli")

    assert lines == [SCHEMA_UPLOAD, CLI_UPLOAD]
