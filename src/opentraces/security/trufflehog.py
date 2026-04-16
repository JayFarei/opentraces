"""TruffleHog Tier 1.5 integration.

Optional secret-scanning tier that runs the ``trufflehog`` binary as a
subprocess, parsing its ``--json`` filesystem output into structured
findings. Complements (does not replace) Tier 1 regex+entropy scanning
by adding 800+ provider detectors.

Enable via ``opentraces setup trufflehog``. Once enabled, a missing
binary is a hard error, not a silent skip — the comment at the top of
``scanner.py`` says it best: "optional tier that quietly stops running"
is exactly how security regressions ship.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class TruffleHogMissingError(RuntimeError):
    """Raised when the ``trufflehog`` binary is enabled-but-absent."""


@dataclass(frozen=True)
class TruffleHogFinding:
    """One detector hit from a TruffleHog JSON line."""

    detector_name: str
    raw_match: str
    verified: bool
    source_file: str
    line_number: int

    def _dedupe_key(self) -> tuple[str, str, bool]:
        return (self.detector_name, self.raw_match, self.verified)


@dataclass
class TruffleHogReport:
    """Aggregate of findings from a single scan invocation."""

    findings: list[TruffleHogFinding] = field(default_factory=list)
    scanned_at: str = ""
    trufflehog_version: str = ""

    @property
    def blocked(self) -> bool:
        """Any finding — verified or not — blocks upload (conservative)."""
        return bool(self.findings)


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


_MISSING_HINT = (
    "'trufflehog' not found on PATH. "
    "Run 'opentraces setup trufflehog' to install and enable, "
    "or 'opentraces setup trufflehog --disable' to turn the tier off."
)


def find_trufflehog() -> str | None:
    """Return the installed trufflehog version string, or ``None`` if absent."""
    binary = shutil.which("trufflehog")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("trufflehog --version failed: %s", exc)
        return None
    output = (result.stdout or result.stderr or "").strip()
    return output or None


def require_trufflehog() -> str:
    """Return the trufflehog version, raising if the binary is missing."""
    version = find_trufflehog()
    if version is None:
        raise TruffleHogMissingError(_MISSING_HINT)
    return version


def available_installers() -> list[str]:
    """Names of installers present on PATH, in preference order."""
    out: list[str] = []
    if shutil.which("brew"):
        out.append("brew")
    if shutil.which("go"):
        out.append("go")
    return out


def _install_via(method: str) -> bool:
    if method == "brew":
        try:
            subprocess.run(["brew", "install", "trufflehog"], check=True)
            return True
        except subprocess.CalledProcessError:
            return False
    if method == "go":
        try:
            subprocess.run(
                ["go", "install",
                 "github.com/trufflesecurity/trufflehog/v3@latest"],
                check=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False
    return False


def install_binary(method: str = "auto") -> tuple[bool, str]:
    """Install the trufflehog binary.

    ``method`` controls which installer is used:
      - ``"auto"`` (default): try brew, then go install.
      - ``"brew"`` / ``"go"``: use that installer only.

    Returns ``(ok, method_used)``. ``method_used`` is the installer that
    succeeded, or ``"none"`` if no installer ran to completion.
    """
    if method == "auto":
        for candidate in available_installers():
            if _install_via(candidate):
                return True, candidate
        return False, "none"
    if method in ("brew", "go"):
        if shutil.which(method) is None:
            return False, "none"
        ok = _install_via(method)
        return (True, method) if ok else (False, "none")
    return False, "none"


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _parse_finding(line: str) -> TruffleHogFinding | None:
    """Parse one JSON line from trufflehog output; return None if malformed."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    try:
        fs = data.get("SourceMetadata", {}).get("Data", {}).get("Filesystem", {})
        return TruffleHogFinding(
            detector_name=str(data.get("DetectorName", "")),
            raw_match=str(data.get("Raw", "")),
            verified=bool(data.get("Verified", False)),
            source_file=str(fs.get("file", "")),
            line_number=int(fs.get("line", 0) or 0),
        )
    except (TypeError, ValueError) as exc:
        logger.debug("Malformed trufflehog finding: %s (%s)", line, exc)
        return None


def scan_file(path: Path, verify: bool = False) -> list[TruffleHogFinding]:
    """Run ``trufflehog filesystem --json`` against ``path``.

    Args:
        path: File or directory to scan.
        verify: When ``True``, allow TruffleHog's live verification probes.
            Default ``False`` — we never assume consent for outbound calls
            from vendor detectors on every scan.

    Returns:
        List of :class:`TruffleHogFinding`. Empty if nothing flagged.

    Raises:
        TruffleHogMissingError: if the binary is not on PATH.
    """
    binary = shutil.which("trufflehog")
    if not binary:
        raise TruffleHogMissingError(_MISSING_HINT)

    cmd = [binary, "filesystem", "--json", "--no-update"]
    if not verify:
        cmd.append("--no-verification")
    cmd.append(str(path))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("trufflehog invocation failed: %s", exc)
        return []

    findings: list[TruffleHogFinding] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        finding = _parse_finding(line)
        if finding is not None:
            findings.append(finding)

    return findings


def scan_trace_jsonl(jsonl_path: Path, verify: bool = False) -> TruffleHogReport:
    """Scan a serialized trace JSONL file and build a deduplicated report.

    Identical ``(detector_name, raw_match, verified)`` tuples are collapsed
    so a secret repeated across steps counts once. Report's ``blocked``
    flag is ``True`` iff any finding surfaced.
    """
    version = require_trufflehog()
    t0 = time.monotonic()
    findings = scan_file(jsonl_path, verify=verify)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Dedupe preserving first occurrence order.
    seen: set[tuple[str, str, bool]] = set()
    unique: list[TruffleHogFinding] = []
    for finding in findings:
        key = finding._dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)

    logger.info(
        "trufflehog scanned %s in %dms (version=%s, findings=%d, verify=%s)",
        jsonl_path, elapsed_ms, version, len(unique), verify,
    )

    return TruffleHogReport(
        findings=unique,
        scanned_at=datetime.now(timezone.utc).isoformat(),
        trufflehog_version=version,
    )
