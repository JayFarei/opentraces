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
        # --no-print-directory keeps recursive sub-makes from emitting
        # "Entering/Leaving directory" lines, which CI's make 4.x enables via
        # MAKEFLAGS=w whenever make runs nested inside another make recipe
        # (e.g. make -> pytest -> this `make -n` grandchild under the sharded
        # test lane). Without it those lines leak into the recipe assertions.
        ["make", "--no-print-directory", "-n", *targets],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith("make[")
    ]


def test_release_cleans_once_before_builds_and_uploads_built_artifacts() -> None:
    lines = _make_dry_run("release")

    assert lines.count(CLEAN) == 1
    assert lines.count(SCHEMA_BUILD) == 1
    assert lines.count(CLI_BUILD) == 1
    assert lines.index(CLEAN) < lines.index(SCHEMA_BUILD)
    assert VIEWER_BUILD not in lines
    assert lines.index(SCHEMA_BUILD) < lines.index(CLI_BUILD)
    assert lines.index(CLI_BUILD) < lines.index(SCHEMA_UPLOAD)
    assert lines.index(SCHEMA_UPLOAD) < lines.index(CLI_UPLOAD)


def test_publish_targets_upload_existing_artifacts_without_rebuilding() -> None:
    lines = _make_dry_run("publish-schema", "publish-cli")

    assert lines == [SCHEMA_UPLOAD, CLI_UPLOAD]
