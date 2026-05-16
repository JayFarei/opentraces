"""Warn-on-stale freshness check for committed capture artifacts (plan 074 R2).

For every committed artifact under ``tests/otbox/captures/<dir>/``, surface
drift signals as ``warnings.warn`` calls (never failures):

1. ``opentraces_schema_version`` differs from the live
   ``opentraces_schema.SCHEMA_VERSION`` (any drift, not just major).
2. ``captured_at`` is more than 90 days old (relative to today).
3. ``scenario_digest`` doesn't match the current scenario TOML at
   ``tests/otbox/simulated_users/scenarios/<scenario_name>.toml``.

The gate is informational only. The OSS default ships with no committed
artifacts (only ``.gitkeep`` + ``README.md``), so the parametrized check
SKIPs cleanly and a single trivial ``assert True`` test remains green.

Once real artifacts ship (plan 073 Mac Mini runner), drift becomes
visible in CI logs without breaking the build.
"""

from __future__ import annotations

import datetime
import json
import warnings
from pathlib import Path

import pytest

CAPTURES_ROOT = Path(__file__).resolve().parent / "captures"
SCENARIOS_ROOT = (
    Path(__file__).resolve().parent / "simulated_users" / "scenarios"
)
STALE_AFTER_DAYS = 90


def _iter_artifacts() -> list[tuple[str, Path]]:
    """Yield ``(artifact_dir_name, metadata_path)`` for every committed
    capture artifact.

    Returns an empty list when ``captures/`` is missing or contains no
    artifact subdirs with a ``metadata.json`` (the OSS default).
    """
    if not CAPTURES_ROOT.exists():
        return []
    out: list[tuple[str, Path]] = []
    for entry in sorted(CAPTURES_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        meta = entry / "metadata.json"
        if meta.is_file():
            out.append((entry.name, meta))
    return out


def test_no_artifacts_is_acceptable():
    """Default-CI smoke test.

    Ensures this file always has at least one passing test even when the
    captures tree is empty (the OSS default). The freshness check itself
    fires per-artifact via the parametrized test below.
    """
    assert True


@pytest.mark.parametrize(
    "artifact_dir_name,metadata_path",
    _iter_artifacts()
    or [
        pytest.param(
            "__none__",
            None,
            marks=pytest.mark.skip(
                reason="no committed capture artifacts (OSS default)"
            ),
        )
    ],
)
def test_capture_artifact_is_fresh(
    artifact_dir_name: str, metadata_path: Path
):
    """For each committed artifact, surface drift signals as warnings."""
    metadata = json.loads(metadata_path.read_text())

    # The scenario name is the canonical key for finding the source TOML
    # — the artifact directory name (artifact_dir_name) can diverge from
    # it. Fall back to the artifact dir name when metadata is incomplete.
    scenario_name = metadata.get("scenario_name") or artifact_dir_name

    # --- 1. Schema version drift -----------------------------------------
    try:
        from opentraces_schema import SCHEMA_VERSION as live_schema
    except Exception as exc:  # noqa: BLE001 - never fail freshness on import
        live_schema = None
        warnings.warn(
            f"capture {scenario_name!r}: could not import "
            f"opentraces_schema.SCHEMA_VERSION ({exc!r}); skipping "
            f"schema-version drift check",
            stacklevel=1,
        )
    captured_schema = metadata.get("opentraces_schema_version", "")
    if live_schema is not None and captured_schema and captured_schema != live_schema:
        warnings.warn(
            f"capture {scenario_name!r}: opentraces_schema_version "
            f"{captured_schema!r} != live {live_schema!r}; consider "
            f"`make capture-refresh SCENARIO={scenario_name}`",
            stacklevel=1,
        )

    # --- 2. Captured-at age ---------------------------------------------
    captured_at = metadata.get("captured_at")
    if captured_at:
        try:
            captured_dt = datetime.datetime.fromisoformat(
                str(captured_at).replace("Z", "+00:00")
            )
            now = datetime.datetime.now(datetime.timezone.utc)
            # Naive datetimes (no tzinfo) compare cleanly against now() once
            # we attach UTC; capture-refresh always writes ISO with offset
            # but be defensive against hand-edited metadata.
            if captured_dt.tzinfo is None:
                captured_dt = captured_dt.replace(tzinfo=datetime.timezone.utc)
            age_days = (now - captured_dt).days
            if age_days > STALE_AFTER_DAYS:
                warnings.warn(
                    f"capture {scenario_name!r}: {age_days} days old "
                    f"(threshold {STALE_AFTER_DAYS}); consider "
                    f"`make capture-refresh SCENARIO={scenario_name}`",
                    stacklevel=1,
                )
        except (ValueError, TypeError) as exc:
            warnings.warn(
                f"capture {scenario_name!r}: malformed captured_at "
                f"{captured_at!r} ({exc!r}); cannot evaluate freshness",
                stacklevel=1,
            )

    # --- 3. Scenario digest drift ---------------------------------------
    scenario_path = SCENARIOS_ROOT / f"{scenario_name}.toml"
    if not scenario_path.exists():
        warnings.warn(
            f"capture {scenario_name!r}: scenario TOML no longer exists "
            f"at {scenario_path}; artifact is orphaned, consider "
            f"removing it or re-running capture-refresh against a "
            f"current scenario",
            stacklevel=1,
        )
    else:
        captured_digest = metadata.get("scenario_digest")
        # scenario_digest(scenario) hashes scenario.source_path.read_bytes()
        # — we go through load_scenario so any future digest-input changes
        # (e.g. normalizing whitespace) are picked up automatically.
        try:
            from tests.otbox.simulated_users.scenario import (
                load_scenario,
                scenario_digest as _scenario_digest,
            )

            live_scenario = load_scenario(scenario_name)
            live_digest = _scenario_digest(live_scenario)
        except Exception as exc:  # noqa: BLE001 - never fail on load issues
            warnings.warn(
                f"capture {scenario_name!r}: could not compute live "
                f"scenario_digest ({exc!r}); skipping digest drift check",
                stacklevel=1,
            )
            live_digest = None
        if (
            live_digest is not None
            and captured_digest
            and captured_digest != live_digest
        ):
            warnings.warn(
                f"capture {scenario_name!r}: scenario_digest "
                f"{captured_digest!r} != live {live_digest!r}; scenario "
                f"TOML has changed since capture, consider "
                f"`make capture-refresh SCENARIO={scenario_name}`",
                stacklevel=1,
            )

    # The freshness gate is informational — warnings are the signal.
    assert True
