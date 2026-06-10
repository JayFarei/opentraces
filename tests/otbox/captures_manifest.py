"""Capture-artifact supply chain (otbox 2.0 phase 0).

Real-agent capture artifacts (``snapshot.tar.gz`` + ``metadata.json``) are
gitignored — before this module they existed only on the machine that ran
``make capture-refresh``, so every fresh checkout silently fell back to the
synthetic harness while believing itself artifact-preferred.

The supply chain:

* ``tests/otbox/captures/manifest.json`` — COMMITTED. One entry per scenario:
  sha256 + size of both files, agent, binary_version, captured_at, source.
  (Scrubbed: no machine-local paths.)
* GitHub release ``otbox-captures-v1`` on the origin repo — carries the
  actual bytes as ``<scenario>.snapshot.tar.gz`` + ``<scenario>.metadata.json``.
* ``./otbox fetch-captures [--scenario NAME]`` — downloads + sha256-verifies
  into the captures root. Hash mismatch is a hard error, never a fallback.
* ``restore_from_capture`` consults the manifest when the local artifact is
  absent and ``OT_OTBOX_FETCH_CAPTURES=1`` — so the nightly lane fetches,
  while the PR lane stays hermetic/synthetic by default.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

RELEASE_TAG = "otbox-captures-v1"
MANIFEST_PATH = Path(__file__).parent / "captures" / "manifest.json"

# Scenarios every healthy checkout is expected to have manifested. Deleting a
# manifest entry cannot silence staleness — this list is the committed floor.
REQUIRED_SCENARIOS = (
    "add-helper-function",
    "codex-linear-edit",
    "pi-linear-edit",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(captures_root: Path) -> dict:
    """Scan ``captures_root`` for complete artifacts and build the manifest."""
    entries = []
    for entry in sorted(captures_root.iterdir()):
        snap = entry / "snapshot.tar.gz"
        meta_path = entry / "metadata.json"
        if not (entry.is_dir() and snap.exists() and meta_path.exists()):
            continue
        meta = json.loads(meta_path.read_text())
        entries.append(
            {
                "scenario": entry.name,
                "snapshot_sha256": _sha256(snap),
                "snapshot_size_bytes": snap.stat().st_size,
                "metadata_sha256": _sha256(meta_path),
                "agent": meta.get("agent"),
                "binary_version": meta.get("binary_version"),
                "captured_at": meta.get("captured_at"),
                "base_checkpoint": meta.get("base_checkpoint"),
                "source": "real-agent",
            }
        )
    return {
        "schema_version": "otbox.captures_manifest.v1",
        "release_tag": RELEASE_TAG,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }


def load_manifest(path: Path = MANIFEST_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def manifest_entry(scenario: str, path: Path = MANIFEST_PATH) -> dict | None:
    doc = load_manifest(path)
    if not doc:
        return None
    for entry in doc.get("entries", []):
        if entry.get("scenario") == scenario:
            return entry
    return None


def fetch_scenario(
    scenario: str,
    captures_root: Path,
    manifest_path: Path = MANIFEST_PATH,
    release_tag: str | None = None,
) -> Path:
    """Download + verify one scenario's artifact pair into ``captures_root``.

    Returns the scenario dir. Raises ``RuntimeError`` on missing manifest
    entry, download failure, or sha256 mismatch (hard-fail, no fallback).
    """
    entry = manifest_entry(scenario, manifest_path)
    if entry is None:
        raise RuntimeError(f"scenario {scenario!r} not in {manifest_path}")
    tag = release_tag or (load_manifest(manifest_path) or {}).get("release_tag", RELEASE_TAG)
    dest = captures_root / scenario
    dest.mkdir(parents=True, exist_ok=True)

    pairs = (
        (f"{scenario}.snapshot.tar.gz", dest / "snapshot.tar.gz", entry["snapshot_sha256"]),
        (f"{scenario}.metadata.json", dest / "metadata.json", entry["metadata_sha256"]),
    )
    for asset, target, want_sha in pairs:
        if target.exists() and _sha256(target) == want_sha:
            continue
        tmp = target.with_suffix(target.suffix + ".fetch")
        tmp.unlink(missing_ok=True)
        proc = subprocess.run(
            ["gh", "release", "download", tag, "--pattern", asset,
             "--output", str(tmp), "--clobber"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"gh release download failed for {asset}: {proc.stderr.strip()}")
        got = _sha256(tmp)
        if got != want_sha:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"sha256 mismatch for {asset}: got {got}, manifest pins {want_sha}"
            )
        os.replace(tmp, target)
    return dest


def fetch_all(captures_root: Path, manifest_path: Path = MANIFEST_PATH) -> list[str]:
    doc = load_manifest(manifest_path)
    if not doc:
        raise RuntimeError(f"no manifest at {manifest_path}")
    fetched = []
    for entry in doc.get("entries", []):
        fetch_scenario(entry["scenario"], captures_root, manifest_path)
        fetched.append(entry["scenario"])
    return fetched
