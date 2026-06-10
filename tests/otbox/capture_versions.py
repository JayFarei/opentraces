"""Harness-version staleness check for real-agent capture artifacts (plan B0).

The agent-harness data-generation engine captures snapshot artifacts by
driving real agent binaries (claude / codex / pi). Every manifest entry
records the ``binary_version`` that produced it (e.g. ``"2.1.143 (Claude
Code)"``). When the installed harness moves past the captured one on
major/minor, the captured worlds may no longer represent what the live
harness emits — the scenario batch is STALE and should be regenerated with
``make capture-refresh SCENARIO=<name>`` (or ``capture-refresh --all
--agent <a>`` for the whole batch).

This module is pure comparison/reporting:

* ``check_capture_versions()`` — one row per manifest entry, with a status
  of ``current`` / ``stale`` / ``binary_absent`` / ``unknown``.
* The CLI surface is ``python -m tests.otbox capture-refresh
  --check-versions [--json]``.
* The nightly amber gate is ``tests/otbox/test_capture_freshness.py``,
  which warns (never fails) on ``stale`` rows — same warn-on-drift
  contract as the other freshness signals there.

CI-safety: when an agent binary is not on PATH the row is
``binary_absent`` (informational), so default CI — which installs no real
agents — reports cleanly and never reds.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .captures_manifest import MANIFEST_PATH, load_manifest

SCHEMA_VERSION = "otbox.capture_versions.v1"

# The in-tree echo meta-binary has no independent release stream; its
# captures can never go version-stale.
_VERSIONLESS_AGENTS = {"echo"}

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_major_minor(text: str | None) -> tuple[int, int] | None:
    """Extract the first ``major.minor`` pair from a version string.

    Handles the harness formats seen in the manifest: ``"2.1.143 (Claude
    Code)"``, ``"codex-cli 0.132.0"``, plain ``"1.2.3"``. Returns ``None``
    when nothing parseable is present.
    """

    if not text:
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def installed_binary_version(binary_name: str) -> str:
    """First non-empty line of ``<binary> --version`` ('' if unavailable)."""

    from .simulated_users.prep import _detect_binary_version

    resolved = shutil.which(binary_name)
    if resolved is None:
        return ""
    return _detect_binary_version(resolved)


def _binary_name_for_agent(agent: str, scenario: str) -> str:
    """Resolve the binary an agent's scenarios drive.

    Prefers the scenario TOML's ``binary_name``; falls back to the agent
    name itself (the convention for claude/codex/pi).
    """

    try:
        import tomllib

        from .simulated_users.scenario import _scenario_path

        doc = tomllib.loads(_scenario_path(scenario).read_text())
        name = str(doc.get("binary_name") or "").strip()
        if name:
            return name
    except Exception:  # noqa: BLE001 - manifest rows may name retired scenarios
        pass
    return agent


def check_capture_versions(
    manifest_path: Path = MANIFEST_PATH,
    *,
    _installed_probe=None,
) -> dict[str, Any]:
    """Compare installed agent binary versions against the capture manifest.

    Returns the ``otbox.capture_versions.v1`` envelope::

        {
          "schema_version": ...,
          "release_tag": <manifest release>,
          "rows": [{scenario, agent, binary_name, captured_version,
                    installed_version, status}],
          "summary": {"current": n, "stale": n, "binary_absent": n,
                       "unknown": n, "versionless": n},
          "stale_scenarios": [...],
        }

    Status semantics (major/minor comparison, per the B0 spec):
      * ``stale``          — installed (major, minor) > captured
      * ``current``        — installed (major, minor) <= captured
      * ``binary_absent``  — agent binary not on PATH (default CI)
      * ``unknown``        — a version string failed to parse
      * ``versionless``    — in-tree agents (echo) that cannot go stale
    """

    probe = _installed_probe or installed_binary_version
    doc = load_manifest(manifest_path) or {}
    entries = doc.get("entries") or []
    rows: list[dict[str, Any]] = []
    installed_cache: dict[str, str] = {}

    for entry in sorted(
        entries, key=lambda e: (str(e.get("agent")), str(e.get("scenario")))
    ):
        agent = str(entry.get("agent") or "")
        scenario = str(entry.get("scenario") or "")
        captured_version = str(entry.get("binary_version") or "")
        if agent in _VERSIONLESS_AGENTS:
            rows.append(
                {
                    "scenario": scenario,
                    "agent": agent,
                    "binary_name": agent,
                    "captured_version": captured_version,
                    "installed_version": "",
                    "status": "versionless",
                }
            )
            continue
        binary_name = _binary_name_for_agent(agent, scenario)
        if binary_name not in installed_cache:
            installed_cache[binary_name] = probe(binary_name)
        installed_version = installed_cache[binary_name]

        if not installed_version:
            status = "binary_absent"
        else:
            captured_mm = parse_major_minor(captured_version)
            installed_mm = parse_major_minor(installed_version)
            if captured_mm is None or installed_mm is None:
                status = "unknown"
            elif installed_mm > captured_mm:
                status = "stale"
            else:
                status = "current"
        rows.append(
            {
                "scenario": scenario,
                "agent": agent,
                "binary_name": binary_name,
                "captured_version": captured_version,
                "installed_version": installed_version,
                "status": status,
            }
        )

    summary = {
        key: sum(1 for r in rows if r["status"] == key)
        for key in ("current", "stale", "binary_absent", "unknown", "versionless")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "release_tag": doc.get("release_tag"),
        "rows": rows,
        "summary": summary,
        "stale_scenarios": [r["scenario"] for r in rows if r["status"] == "stale"],
    }


def format_check_report(report: dict[str, Any]) -> str:
    """Human rendering of ``check_capture_versions`` output."""

    lines = [
        f"capture versions vs installed harnesses "
        f"(release {report.get('release_tag') or 'unknown'}):"
    ]
    for row in report.get("rows", []):
        marker = {
            "stale": "STALE",
            "current": "ok",
            "binary_absent": "absent",
            "unknown": "?",
            "versionless": "-",
        }.get(row["status"], row["status"])
        lines.append(
            f"  [{marker:>6}] {row['agent']:<6} {row['scenario']:<34} "
            f"captured={row['captured_version'] or '?'} "
            f"installed={row['installed_version'] or '-'}"
        )
    s = report.get("summary", {})
    lines.append(
        f"  summary: {s.get('current', 0)} current, {s.get('stale', 0)} stale, "
        f"{s.get('binary_absent', 0)} binary-absent, {s.get('unknown', 0)} unknown, "
        f"{s.get('versionless', 0)} versionless"
    )
    stale = report.get("stale_scenarios") or []
    if stale:
        lines.append(
            "  regenerate stale batches: "
            + ", ".join(f"make capture-refresh SCENARIO={name}" for name in stale)
        )
    return "\n".join(lines)
