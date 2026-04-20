"""Build fictional remote-state fixture directories for scenario tests.

Each builder produces a directory laid out like a HF dataset repo: a
``dataset_infos.json`` (when declared) plus ``data/traces_*.jsonl`` shards.
``FakeHFRepo.from_fixture`` then seeds a writable copy and ``HFUploader``
pushes against it.

Some fixtures are static and live under ``tests/fixtures/remote_states/``
(they intentionally encode versions the current ``TraceRecord`` doesn't know
about, so building from the in-repo model won't produce them). The helpers
here produce the other fixtures — the ones whose shape should track the
current schema — at test time so they don't rot on schema bumps.
"""

from __future__ import annotations

import json
from pathlib import Path

from .test_upload import _make_trace


def _write_dataset_infos(
    workdir: Path,
    *,
    version_str: str,
    features: dict | None = None,
    repo_name: str = "fake-dataset",
) -> None:
    """Write a ``dataset_infos.json`` with the given declared version.

    When ``features`` is ``None``, the current generator is used — that's the
    realistic shape any up-to-date client would publish. Pass an explicit
    ``features`` dict only when the test needs to simulate a narrower schema
    declaration (e.g. an older client's publish).
    """
    if features is None:
        from opentraces.publish.huggingface.schema import HF_FEATURES
        features = HF_FEATURES
    try:
        major, minor, patch = (int(p) for p in version_str.split("."))
    except ValueError:
        major = minor = patch = 0
    payload = {
        "default": {
            "description": "",
            "citation": "",
            "homepage": f"https://huggingface.co/datasets/{repo_name}",
            "license": "",
            "features": features,
            "builder_name": "json",
            "dataset_name": repo_name,
            "config_name": "default",
            "version": {
                "version_str": version_str,
                "major": major,
                "minor": minor,
                "patch": patch,
            },
        }
    }
    (workdir / "dataset_infos.json").write_text(json.dumps(payload, indent=2))


def _write_shard(
    workdir: Path,
    shard_name: str,
    rows: list[dict],
) -> None:
    data_dir = workdir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / shard_name).write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )


def _trace_payload(trace_id: str, *, schema_version: str) -> dict:
    record = _make_trace(trace_id=trace_id)
    payload = record.model_dump(mode="json")
    payload["schema_version"] = schema_version
    return payload


# --- Scenario builders -----------------------------------------------------


def build_state_fresh(workdir: Path) -> Path:
    """Empty remote — no dataset_infos, no shards."""
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def build_state_equal(workdir: Path) -> Path:
    """Remote declared schema equals local; one shard at current version."""
    from opentraces_schema.version import SCHEMA_VERSION

    workdir.mkdir(parents=True, exist_ok=True)
    _write_dataset_infos(workdir, version_str=SCHEMA_VERSION)
    _write_shard(
        workdir,
        "traces_equal_aaaaaaaa.jsonl",
        [_trace_payload("equal-1", schema_version=SCHEMA_VERSION)],
    )
    return workdir


def build_state_older_additive(workdir: Path, older_version: str) -> Path:
    """Remote declared schema is ``older_version``, within the same major.

    Shards contain rows stamped with ``older_version``. Because schema
    evolution is additive, re-parsing these rows through the current
    TraceRecord fills defaults cleanly and the migrator re-stamps them.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    _write_dataset_infos(workdir, version_str=older_version)
    _write_shard(
        workdir,
        "traces_older_bbbbbbbb.jsonl",
        [
            _trace_payload("older-1", schema_version=older_version),
            _trace_payload("older-2", schema_version=older_version),
        ],
    )
    return workdir


def build_state_mixed_versions(workdir: Path, older_version: str) -> Path:
    """One shard carrying rows at current and at ``older_version``.

    After migration only the older rows should be rewritten; current rows
    stay byte-identical on disk.
    """
    from opentraces_schema.version import SCHEMA_VERSION

    workdir.mkdir(parents=True, exist_ok=True)
    _write_dataset_infos(workdir, version_str=SCHEMA_VERSION)
    _write_shard(
        workdir,
        "traces_mixed_cccccccc.jsonl",
        [
            _trace_payload("mixed-old-1", schema_version=older_version),
            _trace_payload("mixed-cur-1", schema_version=SCHEMA_VERSION),
            _trace_payload("mixed-old-2", schema_version=older_version),
            _trace_payload("mixed-cur-2", schema_version=SCHEMA_VERSION),
        ],
    )
    return workdir


# --- Static-fixture paths --------------------------------------------------


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "remote_states"


def static_fixture(name: str) -> Path:
    """Path to a static (hand-written) fixture directory."""
    return FIXTURES_DIR / name


# --- Utilities --------------------------------------------------------------


def previous_minor(version: str) -> str | None:
    """Return ``<major>.<minor-1>.0`` when possible, else ``<major-1>.0.0``.

    Returns ``None`` when the version is unparseable or already at ``0.0.0``.
    Used by scenario tests that want a schema version older than, but
    representative of, the current one.
    """
    parts = version.split(".")
    if len(parts) != 3:
        return None
    try:
        major, minor, _patch = (int(p) for p in parts)
    except ValueError:
        return None
    if minor > 0:
        return f"{major}.{minor - 1}.0"
    if major > 0:
        return f"{major - 1}.0.0"
    return None
