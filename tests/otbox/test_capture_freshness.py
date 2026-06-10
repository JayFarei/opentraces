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

from tests.otbox.checkpoints._captured_helpers import iter_artifacts

SCENARIOS_ROOT = (
    Path(__file__).resolve().parent / "simulated_users" / "scenarios"
)
STALE_AFTER_DAYS = 90


def test_no_artifacts_is_acceptable():
    """Default-CI smoke test.

    Ensures this file always has at least one passing test even when the
    captures tree is empty (the OSS default). The freshness check itself
    fires per-artifact via the parametrized test below.
    """
    assert True


_ARTIFACTS = iter_artifacts() or [
    pytest.param(
        "__none__", None, None,
        marks=pytest.mark.skip(
            reason="no committed capture artifacts (OSS default)"
        ),
    )
]


@pytest.mark.parametrize("artifact_dir_name,archive_path,metadata_path", _ARTIFACTS)
def test_capture_artifact_is_fresh(
    artifact_dir_name: str, archive_path: Path, metadata_path: Path
):
    """For each committed artifact, surface drift signals as warnings."""
    # 0. Orphaned half-committed artifact — surface as drift, then bail
    #    out of the per-field checks (no metadata to walk).
    if not metadata_path.exists():
        warnings.warn(
            f"capture {artifact_dir_name!r}: snapshot.tar.gz committed "
            f"without sibling metadata.json; consider `make capture-refresh "
            f"SCENARIO={artifact_dir_name}` or remove the orphan",
            stacklevel=1,
        )
        return
    if not archive_path.exists():
        warnings.warn(
            f"capture {artifact_dir_name!r}: metadata.json committed "
            f"without sibling snapshot.tar.gz; consider `make capture-refresh "
            f"SCENARIO={artifact_dir_name}` or remove the orphan",
            stacklevel=1,
        )
        return

    metadata = json.loads(metadata_path.read_text())
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


def test_harness_version_staleness_amber_gate():
    """B0 staleness gate: warn (never fail) when an installed agent harness
    has moved past the captured ``binary_version`` on major/minor.

    On substrates with no agent binaries (default CI) every row is
    ``binary_absent`` and the gate is silent. On a developer/runner machine
    with a newer harness installed, the warning names the exact regenerate
    command — the operator signal to run the capture-refresh ritual.
    """
    from tests.otbox.capture_versions import check_capture_versions

    report = check_capture_versions()
    for row in report.get("rows", []):
        if row["status"] == "stale":
            warnings.warn(
                f"capture {row['scenario']!r}: installed {row['agent']} "
                f"harness {row['installed_version']!r} is ahead of captured "
                f"{row['captured_version']!r}; regenerate with "
                f"`make capture-refresh SCENARIO={row['scenario']}` (or "
                f"`make capture-refresh-all AGENT={row['agent']}`)",
                stacklevel=1,
            )
    assert True

    # The freshness gate is informational — warnings are the signal.
    assert True
