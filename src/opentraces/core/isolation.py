"""Isolated-subprocess primitive (issue #184).

ONE shared primitive for running untrusted / semi-trusted child processes with
an HONEST machine-readable label of the isolation *actually* achieved. Consumers:
the workflow script executor (M1, #188) and capsule sandbox v1 (M3, #157). This
module delivers the primitive only; it wires nothing.

Three isolation dimensions, each reported honestly in :class:`IsolationReport`:

  - ``env_scrubbed``   — the child env is built from an ALLOWLIST only (``PATH``,
    ``LANG``, ``LC_*``, ``TERM``, a fresh throwaway ``TMPDIR``, plus caller-supplied
    ``allow_env`` OT_* contract vars). Parent secrets (``*_TOKEN`` / ``*_KEY`` /
    ``*_SECRET`` / ``AWS_*`` / ``HF_*`` / ``ANTHROPIC_*`` / ``OPENAI_*`` / ``GH_*``
    and anything else off the allowlist) are never inherited. Always ``True`` —
    the allowlist construction cannot fail.
  - ``home_isolated``  — ``$HOME`` is redirected to an explicit ``home`` or a fresh
    throwaway dir, never the real HOME. ``True`` whenever the child's HOME differs
    from the operator's real HOME.
  - ``network_denied``  — best-effort OS-level egress denial (darwin ``sandbox-exec``
    with a ``(deny network*)`` profile; linux ``unshare -rn`` / ``unshare -n``).
    The mechanism is *probed* before the claim is made, so ``network_denied`` is
    ``True`` only when a functional deny mechanism was applied. If no mechanism is
    available or the probe fails, the primitive DOES NOT claim denial
    (``network_denied=False``) and logs honestly — it never over-claims.

``sandbox_tier`` uses the ADR-0008 (#178 seal-family) lattice vocabulary:
``none`` (S0), ``jail`` (S1), ``container`` (S2), ``microvm`` (S3). This M1
primitive ALWAYS reports ``none`` (S0). Per the ADR-0008 honesty contract and
the #146/#157 sandbox-v1 scope, a tier above ``none`` requires real OS-level
filesystem containment, and M1 provides none: ``home_isolated`` is only a
``$HOME``-string redirect, so the same-UID child can still read the operator's
real HOME / bucket / config by absolute path or ``pwd.getpwuid(os.getuid())``.
``jail`` (S1) would claim that containment, so it is NEVER emitted in v1 — the
``container`` / ``microvm`` tiers are likewise M3 spike-gated. The three real
signals — ``env_scrubbed``, ``network_denied``, ``home_isolated`` — stay honest
and separate; the added ``filesystem_isolated`` (always False in M1) names the
missing dimension machine-readably. Honesty by construction: the tier can only
rise by delivering real containment, never by relabelling.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# --- ADR-0008 sandbox_tier vocabulary -------------------------------------
# M1 only ever emits SANDBOX_TIER_NONE. SANDBOX_TIER_JAIL is the vocabulary
# name for the S1 rung and is retained for reference / the M3 spike, but the
# primitive never reports it: jail claims filesystem containment that a
# same-UID ``$HOME``-redirect does not provide (C1 / #184).
SANDBOX_TIER_NONE = "none"
SANDBOX_TIER_JAIL = "jail"

# Environment allowlist. Everything NOT matched here is dropped from the child
# env, so parent secrets can never leak by omission-of-a-denylist. ``HOME`` and
# ``TMPDIR`` are set authoritatively by the primitive (isolation), so they are
# deliberately absent from the inherited-passthrough set below.
_ENV_ALLOW_EXACT = frozenset({"PATH", "LANG", "TERM", "TZ"})
_ENV_ALLOW_PREFIX = ("LC_",)

# macOS Seatbelt profile: allow everything except network operations.
_DARWIN_DENY_PROFILE = "(version 1)(allow default)(deny network*)"

# Exit code stamped on a timed-out run (GNU ``timeout`` convention).
_TIMEOUT_RETURNCODE = 124

# Bound on the mechanism-availability probe.
_PROBE_TIMEOUT = 15.0


@dataclass(frozen=True)
class IsolationReport:
    """Honest label of the isolation a run actually achieved.

    Every field reflects the OS-level reality of the run, never the request:
    ``network_denied`` is ``True`` only if a functional deny mechanism was
    applied. ``filesystem_isolated`` is the honest missing-dimension flag —
    always ``False`` in M1 because a ``$HOME``-string redirect is not real
    containment — and ``sandbox_tier`` stays ``none`` in v1 for the same reason
    (a tier above ``none`` requires real filesystem containment; C1 / #184).
    """

    env_scrubbed: bool
    network_denied: bool
    home_isolated: bool
    filesystem_isolated: bool
    sandbox_tier: str


@dataclass(frozen=True)
class IsolatedResult:
    """Outcome of an isolated run plus the honest :class:`IsolationReport`."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    achieved: IsolationReport


def run_isolated(
    cmd: list[str],
    *,
    cwd: str | os.PathLike[str],
    allow_env: dict[str, str] | None = None,
    network: str = "deny",
    home: str | os.PathLike[str] | None = None,
    timeout: float = 180,
    source_trust: str = "untrusted",
) -> IsolatedResult:
    """Run ``cmd`` as an isolated child process and report what was achieved.

    Parameters
    ----------
    cmd:
        The argv to run (no shell). It is wrapped in the platform network-deny
        launcher when ``network="deny"`` and a functional mechanism exists.
    cwd:
        Working directory for the child.
    allow_env:
        Caller-supplied contract variables (OT_* etc.) to add to the scrubbed
        env. These are the ONLY non-allowlisted keys that reach the child.
    network:
        ``"deny"`` (best-effort OS egress denial) or ``"allow"`` (no wrapping).
    home:
        Explicit throwaway HOME for the child; a fresh throwaway dir is created
        when ``None``. Either way ``$HOME`` never points at the real HOME.
    timeout:
        Seconds before the child is killed; a timeout sets ``timed_out=True``.
    source_trust:
        Advisory label of how much the command's source is trusted (reserved for
        consumer policy such as #188 bundled-vs-third-party). It does NOT weaken
        the isolation applied here — isolation is always enforced.

    Returns
    -------
    IsolatedResult
        The child's returncode/stdout/stderr plus an honest ``achieved`` report.
    """
    if network not in ("deny", "allow"):
        raise ValueError(f"network must be 'deny' or 'allow', got {network!r}")

    # A single scratch root holds the throwaway HOME and TMPDIR so cleanup is one
    # rmtree. An explicit ``home`` lives outside it and is left untouched.
    scratch = Path(tempfile.mkdtemp(prefix="ot-isolation-"))
    try:
        child_tmp = scratch / "tmp"
        child_tmp.mkdir()

        if home is not None:
            child_home = Path(home)
            child_home.mkdir(parents=True, exist_ok=True)
        else:
            child_home = scratch / "home"
            child_home.mkdir()

        env = _build_child_env(allow_env, child_home, child_tmp)
        env_scrubbed = True  # allowlist construction cannot fail
        home_isolated = _is_home_isolated(child_home)

        wrapped_cmd, network_denied = _apply_network_deny(list(cmd), network)

        # C1 / #184: M1 provides no OS-level filesystem containment (a
        # ``$HOME``-string redirect leaves the real HOME / bucket readable by
        # absolute path to a same-UID child), so the honest tier is ALWAYS
        # ``none`` (S0). ``jail`` would over-claim containment; it is never
        # emitted in v1. ``filesystem_isolated`` records the missing dimension.
        filesystem_isolated = False
        sandbox_tier = SANDBOX_TIER_NONE
        report = IsolationReport(
            env_scrubbed=env_scrubbed,
            network_denied=network_denied,
            home_isolated=home_isolated,
            filesystem_isolated=filesystem_isolated,
            sandbox_tier=sandbox_tier,
        )

        try:
            proc = subprocess.run(
                wrapped_cmd,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            return IsolatedResult(
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                timed_out=False,
                achieved=report,
            )
        except subprocess.TimeoutExpired as exc:
            return IsolatedResult(
                returncode=_TIMEOUT_RETURNCODE,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
                timed_out=True,
                achieved=report,
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# --- internals ------------------------------------------------------------


def _build_child_env(
    allow_env: dict[str, str] | None,
    child_home: Path,
    child_tmp: Path,
) -> dict[str, str]:
    """Construct the child env from the allowlist only.

    Order matters: allow-listed passthrough first, then caller ``allow_env``,
    then HOME/TMPDIR set LAST so the isolation guarantee cannot be subverted by
    a stray ``HOME`` / ``TMPDIR`` in ``allow_env``.
    """
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _ENV_ALLOW_EXACT or any(key.startswith(p) for p in _ENV_ALLOW_PREFIX):
            env[key] = value

    if allow_env:
        env.update(allow_env)

    env["HOME"] = str(child_home)
    env["TMPDIR"] = str(child_tmp)
    env.setdefault("PATH", os.environ.get("PATH", ""))
    return env


def _is_home_isolated(child_home: Path) -> bool:
    """True iff the child's HOME differs from the operator's real HOME."""
    real_home = os.environ.get("HOME") or os.path.expanduser("~")
    try:
        return os.path.realpath(str(child_home)) != os.path.realpath(real_home)
    except OSError:
        return str(child_home) != real_home


def _apply_network_deny(cmd: list[str], network: str) -> tuple[list[str], bool]:
    """Return ``(wrapped_cmd, network_denied)``.

    Best-effort and honest: the platform mechanism is probed for functionality
    before the deny claim is made. If no mechanism works, the command runs
    unwrapped and ``network_denied`` is ``False``.
    """
    if network != "deny":
        return cmd, False

    if sys.platform == "darwin":
        sandbox_exec = shutil.which("sandbox-exec") or "/usr/bin/sandbox-exec"
        prefix = [sandbox_exec, "-p", _DARWIN_DENY_PROFILE]
        if os.path.exists(sandbox_exec) and _probe_launcher(prefix):
            return [*prefix, *cmd], True
        logger.warning(
            "network=deny requested but sandbox-exec is unavailable/non-functional; "
            "running without network denial (network_denied=False)"
        )
        return cmd, False

    if sys.platform.startswith("linux"):
        prefix = _linux_deny_prefix()
        if prefix is not None:
            return [*prefix, *cmd], True
        logger.warning(
            "network=deny requested but no functional unshare namespace mechanism "
            "is available; running without network denial (network_denied=False)"
        )
        return cmd, False

    logger.warning(
        "network=deny requested but no deny mechanism exists on platform %s; "
        "running without network denial (network_denied=False)",
        sys.platform,
    )
    return cmd, False


def _linux_deny_prefix() -> list[str] | None:
    """Probe unshare namespace launchers; return the first that works, else None."""
    unshare = shutil.which("unshare")
    if not unshare:
        return None
    # Rootless user+net namespace first, then a privileged net-only namespace.
    for flags in (["-rn"], ["-n"]):
        prefix = [unshare, *flags]
        if _probe_launcher(prefix):
            return prefix
    return None


def _probe_launcher(prefix: list[str]) -> bool:
    """True iff ``prefix`` can initialize and run a trivial child successfully.

    This validates that the isolation launcher (sandbox-exec / unshare) actually
    works on THIS host — the difference between "wrapped" and "denied". It does
    not perform a network attempt (the deny profile/namespace semantics are
    well-known); the socket-level assertion lives in the test suite.
    """
    try:
        proc = subprocess.run(
            [*prefix, sys.executable, "-c", "print('OK')"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "OK" in proc.stdout


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
