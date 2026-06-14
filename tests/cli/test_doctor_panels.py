"""Unit tests for plan-043 phase-7 doctor panels (attribution + watcher).

Exercise the private helpers directly so rendering / config wiring stays
testable without going through Click."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from opentraces.core import doctor


# --- attribution panel -----------------------------------------------------

def test_attribution_no_project(tmp_path: Path) -> None:
    info = doctor._attribution_status(tmp_path)
    assert info["health"] == "no-project"
    assert info["cached_commits"] == 0
    assert info["project_slug"] is None


def test_attribution_empty_cache_when_opted_in(tmp_path: Path) -> None:
    # Marker only — no attributions written yet.
    (tmp_path / ".opentraces.json").write_text('{"project_id": "abc", "policy": {}}')
    with mock.patch.object(doctor, "project_is_opted_in", return_value=True), \
         mock.patch("opentraces.core.cache.AttributionCache.list_attributed_shas",
                    return_value=[]):
        info = doctor._attribution_status(tmp_path)
    assert info["health"] == "empty"
    assert info["cached_commits"] == 0


def test_attribution_ok_when_cache_populated(tmp_path: Path) -> None:
    (tmp_path / ".opentraces.json").write_text('{"project_id": "abc", "policy": {}}')
    with mock.patch.object(doctor, "project_is_opted_in", return_value=True), \
         mock.patch("opentraces.core.cache.AttributionCache.list_attributed_shas",
                    return_value=["aa", "bb", "cc"]):
        info = doctor._attribution_status(tmp_path)
    assert info["cached_commits"] == 3
    assert info["health"] in ("ok", "stale")  # depends on whether last_backfill_at set


# --- watcher panel ---------------------------------------------------------

def _fake_status(*, installed, running, plat="macos"):
    from types import SimpleNamespace
    return SimpleNamespace(
        platform=plat,
        installed=installed,
        running=running,
        last_run_at=None,
        interval_seconds=300 if installed else None,
        unit_path=Path("/tmp/fake.plist") if installed else None,
    )


def test_watcher_not_installed() -> None:
    with mock.patch("opentraces.watcher.installer.status",
                    return_value=_fake_status(installed=False, running=False)):
        info = doctor._watcher_status()
    assert info["health"] == "not-installed"
    assert info["installed"] is False
    assert info["platform"] == "macos"


def test_watcher_installed_not_running() -> None:
    with mock.patch("opentraces.watcher.installer.status",
                    return_value=_fake_status(installed=True, running=False)):
        info = doctor._watcher_status()
    assert info["health"] == "not-running"
    assert info["installed"] is True


def test_watcher_ok() -> None:
    with mock.patch("opentraces.watcher.installer.status",
                    return_value=_fake_status(installed=True, running=True)):
        info = doctor._watcher_status()
    assert info["health"] == "ok"
    assert info["interval_seconds"] == 300
    assert info["unit_path"] == "/tmp/fake.plist"


def test_watcher_unsupported_platform() -> None:
    with mock.patch("opentraces.watcher.installer.status",
                    side_effect=RuntimeError("unsupported")):
        info = doctor._watcher_status()
    assert info["health"] == "unsupported-platform"
    assert info["platform"] == "unsupported"


# --- report() shape --------------------------------------------------------

def test_report_includes_new_panels(tmp_path: Path) -> None:
    """doctor.report() must include attribution + watcher keys."""
    from opentraces.core.config import load_config

    cfg = load_config()
    with mock.patch("opentraces.watcher.installer.status",
                    return_value=_fake_status(installed=False, running=False)):
        rpt = doctor.report(cfg, tmp_path)
    assert "attribution" in rpt
    assert "watcher" in rpt
    assert "entity_parser" in rpt  # pre-existing panel still present
    assert rpt["attribution"]["health"] == "no-project"
    assert rpt["watcher"]["health"] == "not-installed"


def test_report_counts_project_identity_sidecars(tmp_path: Path) -> None:
    """doctor.report() includes projects known only via watcher sidecars."""
    from opentraces.core import paths
    from opentraces.core.config import load_config

    project = tmp_path / "project"
    project.mkdir()
    slug_dir = paths.PROJECTS_DIR / "project-abc"
    slug_dir.mkdir(parents=True)
    (slug_dir / "project.json").write_text(json.dumps({"path": str(project)}))

    cfg = load_config()
    assert getattr(cfg, "projects", {}) == {}
    with mock.patch("opentraces.watcher.installer.status",
                    return_value=_fake_status(installed=False, running=False)):
        rpt = doctor.report(cfg, tmp_path)

    assert str(project.resolve()) in rpt["opted_in_projects"]["paths"]


def test_bucket_status_reads_persisted_manifest_without_regeneration(
    monkeypatch,
) -> None:
    """doctor's bucket panel must not run the full bucket manifest scan."""
    from opentraces.core import paths

    manifest_path = paths.bucket_dir() / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "opentraces.bucket.manifest.v2",
                "root": str(paths.bucket_dir()),
                "digest": "sha256:persisted",
                "bucket_digest": "sha256:persisted",
                "trace_records": {"object_count": 7},
                "trail": {"stale_count": 0},
                "sync": {"eligible": True},
                "context_trees": {
                    "trace_count": 2,
                    "unique_layer_blob_count": 5,
                    "dangling_layer_refs_count": 0,
                },
            }
        )
    )

    def fail_manifest(*args, **kwargs):
        raise AssertionError("doctor must not regenerate bucket_manifest")

    monkeypatch.setattr("opentraces.core.bucket_store.bucket_manifest", fail_manifest)

    info = doctor._bucket_status()

    assert info["state"] == "ok"
    assert info["digest"] == "sha256:persisted"
    assert info["trace_records"]["object_count"] == 7
    assert info["context_tree"]["trace_count"] == 2
    assert info["context_tree"]["layer_blob_count"] == 5
