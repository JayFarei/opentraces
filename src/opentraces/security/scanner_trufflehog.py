"""Tier 1.5 integration glue.

Thin wrapper that gates TruffleHog execution on :class:`TruffleHogConfig`.
Kept in its own module so the rest of ``scanner.py`` stays free of
subprocess concerns and so tests can exercise the gate in isolation.
"""

from __future__ import annotations

from pathlib import Path

from ..config import TruffleHogConfig
from .trufflehog import TruffleHogReport, scan_trace_jsonl


def maybe_run_trufflehog(
    jsonl_path: Path,
    config: TruffleHogConfig,
) -> TruffleHogReport | None:
    """Run the Tier 1.5 scan iff ``config.enabled`` is true.

    Returns ``None`` when the tier is disabled. When enabled, defers to
    :func:`scan_trace_jsonl` which raises :class:`TruffleHogMissingError`
    if the binary is absent — no silent skip once opted in.
    """
    if not config.enabled:
        return None
    return scan_trace_jsonl(jsonl_path, verify=config.verify_secrets)
