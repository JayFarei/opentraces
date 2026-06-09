"""Supply-chain contract for real-agent capture artifacts (otbox 2.0 phase 0)."""

from __future__ import annotations

import json
import re

from .captures_manifest import (
    MANIFEST_PATH,
    REQUIRED_SCENARIOS,
    build_manifest,
    load_manifest,
    manifest_entry,
)

_SHA = re.compile(r"^[0-9a-f]{64}$")


def test_committed_manifest_is_valid_and_complete():
    doc = load_manifest()
    assert doc, f"no committed manifest at {MANIFEST_PATH}"
    assert doc["schema_version"] == "otbox.captures_manifest.v1"
    assert doc["release_tag"]
    names = {e["scenario"] for e in doc["entries"]}
    for required in REQUIRED_SCENARIOS:
        assert required in names, (
            f"required scenario {required!r} missing from manifest — deleting a "
            f"manifest entry cannot silence the staleness floor"
        )
    for e in doc["entries"]:
        assert _SHA.match(e["snapshot_sha256"]), e["scenario"]
        assert _SHA.match(e["metadata_sha256"]), e["scenario"]
        assert e["snapshot_size_bytes"] > 0
        assert e["captured_at"], e["scenario"]
        assert e["source"] == "real-agent"


def test_build_manifest_scans_complete_artifacts_only(tmp_path):
    full = tmp_path / "scenario-full"
    full.mkdir()
    (full / "snapshot.tar.gz").write_bytes(b"tarball-bytes")
    (full / "metadata.json").write_text(json.dumps(
        {"agent": "codex", "binary_version": "codex-cli 0.1",
         "captured_at": "2026-06-10T00:00:00+00:00", "base_checkpoint": "c-x"}))
    half = tmp_path / "scenario-half"
    half.mkdir()
    (half / "metadata.json").write_text("{}")

    doc = build_manifest(tmp_path)
    assert [e["scenario"] for e in doc["entries"]] == ["scenario-full"]
    entry = doc["entries"][0]
    assert entry["agent"] == "codex"
    assert _SHA.match(entry["snapshot_sha256"])
    # no machine-local paths leak into the manifest
    assert "binary_path" not in entry


def test_manifest_entry_lookup(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "schema_version": "otbox.captures_manifest.v1",
        "release_tag": "t",
        "entries": [{"scenario": "a"}],
    }))
    assert manifest_entry("a", manifest) == {"scenario": "a"}
    assert manifest_entry("b", manifest) is None
