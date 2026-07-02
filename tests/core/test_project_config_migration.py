"""Tests for ProjectConfig schema migration: single-remote -> remotes dict.

Step 1 of the CLI restructure plan (kb/plans/buzzing-dreaming-forest.md).

The legacy ProjectConfig had `remote: str | None` + `visibility: str` (single).
The new shape has `remotes: dict[str, RemoteConfig]` + `active_remote: str | None`.
This test file covers the migration in both directions:

- Load a legacy marker and confirm the shape is normalized to the new schema.
- Save a new-shape ProjectConfig and confirm the on-disk JSON has the new keys
  (and no legacy ones).
- Backward-compat: existing callers that read `data["remote"]` / `data["visibility"]`
  via load_project_config() still work via synthesis from the active remote.
- Round-trip idempotence: load -> save -> load yields equivalent data.
"""

from __future__ import annotations

import json

import pytest

from opentraces.core.config import (
    MARKER_VERSION,
    ProjectConfig,
    RemoteConfig,
    load_project_config,
    save_project_config,
)
from opentraces.core.paths import MARKER_FILENAME


def _write_legacy_marker(project_dir, *, remote="hf://me/dataset", visibility="private"):
    """Write a marker in the legacy single-remote format (marker_version=1)."""
    payload = {
        "marker_version": "1",
        "project_id": "abc123",
        "review_policy": "review",
        "push_policy": "manual",
        "remote": remote,
        "visibility": visibility,
        "agents": ["claude-code"],
    }
    marker = project_dir / MARKER_FILENAME
    marker.write_text(json.dumps(payload, indent=2))


class TestRemoteConfigModel:
    def test_remote_config_has_url_and_visibility(self) -> None:
        rc = RemoteConfig(url="hf://me/dataset", visibility="private")
        assert rc.url == "hf://me/dataset"
        assert rc.visibility == "private"

    def test_remote_config_default_visibility_private(self) -> None:
        rc = RemoteConfig(url="hf://me/dataset")
        assert rc.visibility == "private"

    def test_remote_config_rejects_invalid_visibility(self) -> None:
        with pytest.raises(Exception):  # pydantic ValidationError
            RemoteConfig(url="hf://me/dataset", visibility="secret")


class TestProjectConfigShape:
    def test_project_config_has_remotes_and_active_remote(self) -> None:
        cfg = ProjectConfig()
        assert cfg.remotes == {}
        assert cfg.active_remote is None

    def test_project_config_accepts_remotes_dict(self) -> None:
        cfg = ProjectConfig(
            remotes={"origin": RemoteConfig(url="hf://me/x", visibility="private")},
            active_remote="origin",
        )
        assert "origin" in cfg.remotes
        assert cfg.remotes["origin"].url == "hf://me/x"
        assert cfg.active_remote == "origin"


class TestLegacyMarkerMigration:
    def test_load_legacy_marker_produces_remotes_dict(self, tmp_path) -> None:
        _write_legacy_marker(tmp_path, remote="hf://me/legacy", visibility="public")

        data = load_project_config(tmp_path)

        assert "remotes" in data
        assert data["remotes"] == {"origin": {"url": "hf://me/legacy", "visibility": "public"}}
        assert data["active_remote"] == "origin"

    def test_load_legacy_marker_with_no_remote_yields_empty_remotes(self, tmp_path) -> None:
        payload = {
            "marker_version": "1",
            "project_id": "noremote",
            "review_policy": "review",
            "agents": ["claude-code"],
        }
        (tmp_path / MARKER_FILENAME).write_text(json.dumps(payload))

        data = load_project_config(tmp_path)

        assert data.get("remotes") == {}
        assert data.get("active_remote") is None

    def test_load_legacy_marker_preserves_other_fields(self, tmp_path) -> None:
        _write_legacy_marker(tmp_path, remote="hf://me/x", visibility="public")

        data = load_project_config(tmp_path)

        assert data["review_policy"] == "review"
        assert "push_policy" not in data  # dead field deleted (#160)
        assert data["agents"] == ["claude-code"]


class TestBackwardCompatLegacyKeys:
    """Existing callers in cli/__init__.py, cli/publish.py, etc. read
    `data.get("remote")` and `data.get("visibility")`. Until step 4 migrates
    them, load_project_config must continue to expose these legacy keys,
    derived from the active remote when present."""

    def test_load_legacy_marker_synthesizes_legacy_remote_key(self, tmp_path) -> None:
        _write_legacy_marker(tmp_path, remote="hf://me/legacy", visibility="public")

        data = load_project_config(tmp_path)

        assert data.get("remote") == "hf://me/legacy"
        assert data.get("visibility") == "public"

    def test_save_new_shape_then_load_synthesizes_legacy_keys(self, tmp_path) -> None:
        save_project_config(
            tmp_path,
            {
                "review_policy": "review",
                "remotes": {"origin": {"url": "hf://me/dataset", "visibility": "private"}},
                "active_remote": "origin",
            },
        )

        data = load_project_config(tmp_path)

        # New keys present
        assert data["remotes"] == {"origin": {"url": "hf://me/dataset", "visibility": "private"}}
        assert data["active_remote"] == "origin"
        # Legacy keys synthesized for back-compat with existing callers
        assert data.get("remote") == "hf://me/dataset"
        assert data.get("visibility") == "private"


class TestSaveOnlyNewKeys:
    def test_save_new_shape_writes_only_new_keys_to_disk(self, tmp_path) -> None:
        save_project_config(
            tmp_path,
            {
                "review_policy": "review",
                "remotes": {"origin": {"url": "hf://me/dataset", "visibility": "private"}},
                "active_remote": "origin",
            },
        )

        on_disk = json.loads((tmp_path / MARKER_FILENAME).read_text())

        # New keys are persisted
        assert on_disk["remotes"] == {
            "origin": {"url": "hf://me/dataset", "visibility": "private"}
        }
        assert on_disk["active_remote"] == "origin"
        # Legacy keys are NOT persisted (migration is one-way on save)
        assert "remote" not in on_disk
        assert "visibility" not in on_disk
        # Marker version bumped
        assert on_disk["marker_version"] == MARKER_VERSION

    def test_save_legacy_shape_is_migrated_then_persisted_as_new(self, tmp_path) -> None:
        """Callers can still pass legacy {remote, visibility} during transition;
        save normalizes them to the new shape on disk."""
        save_project_config(
            tmp_path,
            {
                "review_policy": "review",
                "remote": "hf://me/dataset",
                "visibility": "public",
            },
        )

        on_disk = json.loads((tmp_path / MARKER_FILENAME).read_text())

        assert on_disk["remotes"] == {
            "origin": {"url": "hf://me/dataset", "visibility": "public"}
        }
        assert on_disk["active_remote"] == "origin"
        assert "remote" not in on_disk
        assert "visibility" not in on_disk


class TestReadPurity:
    """``load_project_config`` is a pure read (issue #60 item 2).

    Normalization/migration happens in memory only; the on-disk marker —
    often git-committed — is healed exclusively by explicit write verbs
    (``opentraces init``, ``config set ... --project``, i.e.
    ``save_project_config``). Same family as issue #55.
    """

    def test_load_project_config_never_rewrites_id_carrying_marker(
        self, tmp_path
    ) -> None:
        _write_legacy_marker(tmp_path, remote="hf://me/legacy", visibility="public")
        marker = tmp_path / MARKER_FILENAME
        before = marker.read_bytes()

        data = load_project_config(tmp_path)

        # The returned dict carries the migrated shape...
        assert data["remotes"] == {
            "origin": {"url": "hf://me/legacy", "visibility": "public"}
        }
        assert data["active_remote"] == "origin"
        # ...but the marker on disk is byte-identical: reads never write.
        assert marker.read_bytes() == before

    def test_load_project_config_never_rewrites_modern_marker_missing_defaults(
        self, tmp_path
    ) -> None:
        payload = {
            "marker_version": "2",
            "project_id": "moderndefaults",
            "review_policy": "review",
        }
        marker = tmp_path / MARKER_FILENAME
        marker.write_text(json.dumps(payload, indent=2))
        before = marker.read_bytes()

        data = load_project_config(tmp_path)

        # Defaults are backfilled in memory...
        assert data["remotes"] == {}
        assert data["active_remote"] is None
        # ...without touching the marker.
        assert marker.read_bytes() == before

    def test_set_root_commit_sha_preserves_legacy_remote_keys(
        self, tmp_path
    ) -> None:
        """The latent drop: a never-rewritten legacy marker reaching the
        setter must not lose its remote. The setter used to rebuild policy
        from ``_PORTABLE_FIELDS`` only, which excludes the legacy
        ``remote`` / ``visibility`` keys — they were silently dropped."""
        from opentraces.core.config import set_root_commit_sha

        _write_legacy_marker(tmp_path, remote="hf://me/keepme", visibility="public")

        set_root_commit_sha(tmp_path, "a" * 40)

        on_disk = json.loads((tmp_path / MARKER_FILENAME).read_text())
        assert on_disk["root_commit_sha"] == "a" * 40
        assert on_disk["remotes"] == {
            "origin": {"url": "hf://me/keepme", "visibility": "public"}
        }
        assert on_disk["active_remote"] == "origin"
        # Migration is one-way: legacy keys never written back.
        assert "remote" not in on_disk
        assert "visibility" not in on_disk

    def test_save_after_load_persists_normalization(self, tmp_path) -> None:
        """The explicit heal path: load (pure) then an explicit write verb
        persists the migrated shape with the original project_id."""
        _write_legacy_marker(tmp_path, remote="hf://me/x", visibility="private")

        data = load_project_config(tmp_path)
        save_project_config(tmp_path, data)

        on_disk = json.loads((tmp_path / MARKER_FILENAME).read_text())
        assert on_disk["project_id"] == "abc123"
        assert on_disk["remotes"] == {
            "origin": {"url": "hf://me/x", "visibility": "private"}
        }
        assert on_disk["active_remote"] == "origin"
        assert "remote" not in on_disk
        assert "visibility" not in on_disk


class TestRoundTripIdempotent:
    def test_legacy_load_save_load_yields_equivalent_data(self, tmp_path) -> None:
        _write_legacy_marker(tmp_path, remote="hf://me/x", visibility="private")

        first = load_project_config(tmp_path)
        save_project_config(tmp_path, first)
        second = load_project_config(tmp_path)

        assert first.get("remotes") == second.get("remotes")
        assert first.get("active_remote") == second.get("active_remote")
        assert first.get("review_policy") == second.get("review_policy")

    def test_marker_version_bumps_on_save_after_legacy_load(self, tmp_path) -> None:
        _write_legacy_marker(tmp_path)

        load_project_config(tmp_path)  # pure read: normalizes in memory only
        save_project_config(
            tmp_path, {"remotes": {"origin": {"url": "hf://me/x", "visibility": "private"}}, "active_remote": "origin"}
        )

        on_disk = json.loads((tmp_path / MARKER_FILENAME).read_text())
        assert on_disk["marker_version"] == MARKER_VERSION


class TestMarkerVersionBump:
    def test_marker_version_constant_is_bumped(self) -> None:
        # Either "2" (string) or higher when we move to ints; just ensure it's not "1".
        assert MARKER_VERSION != "1", "MARKER_VERSION should be bumped past '1' for the remotes migration"
