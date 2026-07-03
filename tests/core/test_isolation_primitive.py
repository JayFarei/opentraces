"""RED-first spec for the isolated-subprocess primitive (issue #184).

The primitive (``opentraces.core.isolation.run_isolated``) runs an
untrusted / semi-trusted child process and returns an HONEST machine-readable
label of the isolation actually achieved. These tests ARE the spec:

  - a planted parent-env secret is invisible to the child (env scrub);
  - a socket-connect from the child FAILS under ``network="deny"`` *and* the
    reported ``network_denied`` matches that reality (socket-level, not a flag),
    skip-guarded when no OS mechanism is available;
  - the ``sandbox_tier`` label is honest — a run without a real network-deny
    never claims ``jail``, and M1 never emits ``container`` / ``microvm``;
  - the child's ``$HOME`` is a throwaway / explicit dir, never the real HOME.
"""
from __future__ import annotations

import os
import sys

import pytest

from opentraces.core.isolation import (
    IsolatedResult,
    IsolationReport,
    run_isolated,
)


# --- env scrub ------------------------------------------------------------


def test_planted_token_invisible_to_child(tmp_path, monkeypatch):
    """A parent *_TOKEN / *_SECRET is never inherited; allow_env OT_* + PATH are."""
    monkeypatch.setenv("HF_TOKEN", "planted-SENTINEL-HF")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "planted-SENTINEL-AWS")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "planted-SENTINEL-ANTHROPIC")

    child = (
        "import os\n"
        "print('HF=' + os.environ.get('HF_TOKEN', 'ABSENT'))\n"
        "print('AWS=' + os.environ.get('AWS_SECRET_ACCESS_KEY', 'ABSENT'))\n"
        "print('ANTHROPIC=' + os.environ.get('ANTHROPIC_API_KEY', 'ABSENT'))\n"
        "print('PATH_OK=' + ('yes' if os.environ.get('PATH') else 'no'))\n"
        "print('OT=' + os.environ.get('OT_CONTRACT_VAR', 'ABSENT'))\n"
    )
    result = run_isolated(
        [sys.executable, "-c", child],
        cwd=str(tmp_path),
        allow_env={"OT_CONTRACT_VAR": "ot-visible"},
        network="allow",
    )

    assert isinstance(result, IsolatedResult)
    assert result.returncode == 0, result.stderr
    # The sentinels must not appear ANYWHERE in the child's output.
    assert "planted-SENTINEL-HF" not in result.stdout
    assert "planted-SENTINEL-AWS" not in result.stdout
    assert "planted-SENTINEL-ANTHROPIC" not in result.stdout
    # The child positively observed ABSENT for each scrubbed secret.
    assert "HF=ABSENT" in result.stdout
    assert "AWS=ABSENT" in result.stdout
    assert "ANTHROPIC=ABSENT" in result.stdout
    # Allow-listed carriers ARE visible.
    assert "PATH_OK=yes" in result.stdout
    assert "OT=ot-visible" in result.stdout
    # env scrub always succeeds.
    assert result.achieved.env_scrubbed is True


# --- network deny (socket-level, skip-guarded) ----------------------------


def test_socket_connect_blocked_under_deny(tmp_path):
    """A real socket.connect under deny fails, AND the claim matches reality."""
    child = (
        "import socket, sys\n"
        "s = socket.socket(); s.settimeout(5)\n"
        "try:\n"
        "    s.connect(('1.1.1.1', 443))\n"
        "    print('CONNECTED')\n"
        "    sys.exit(0)\n"
        "except Exception as e:\n"
        "    print('BLOCKED:' + type(e).__name__)\n"
        "    sys.exit(7)\n"
        "finally:\n"
        "    s.close()\n"
    )
    result = run_isolated(
        [sys.executable, "-c", child], cwd=str(tmp_path), network="deny"
    )

    if not result.achieved.network_denied:
        pytest.skip(
            "no OS network-deny mechanism available/functional on this platform; "
            "primitive honestly reports network_denied=False (no over-claim)"
        )

    # The claim is network_denied=True -> reality must match: the connect FAILED.
    assert result.achieved.network_denied is True
    assert result.returncode != 0
    assert "CONNECTED" not in result.stdout
    assert "BLOCKED:" in result.stdout


def test_network_allow_does_not_claim_denial(tmp_path):
    result = run_isolated(
        [sys.executable, "-c", "print('ok')"],
        cwd=str(tmp_path),
        network="allow",
    )
    assert result.returncode == 0
    assert result.achieved.network_denied is False


# --- honest sandbox_tier --------------------------------------------------


def test_tier_none_without_network_deny(tmp_path):
    """env + home isolation alone is NOT a jail."""
    result = run_isolated(
        [sys.executable, "-c", "print('hi')"],
        cwd=str(tmp_path),
        network="allow",
    )
    assert result.achieved.network_denied is False
    assert result.achieved.sandbox_tier != "jail"
    assert result.achieved.sandbox_tier == "none"


def test_fully_hardened_run_reports_none_never_jail(tmp_path):
    """C1 (#184): env-scrub + network-deny + home-redirect is NOT filesystem
    containment. The same-UID child can still read the real HOME / bucket by
    absolute path or ``pwd.getpwuid(os.getuid()).pw_dir``, so the single
    ``sandbox_tier`` token must stay ``none`` (S0) in M1 and MUST NOT over-claim
    ``jail``. ``home_isolated`` is only a ``$HOME``-string redirect, honest as a
    separate signal but not containment. This assertion is platform-independent:
    the tier is ``none`` whether or not an OS network-deny mechanism exists,
    because filesystem containment is the missing dimension either way."""
    result = run_isolated(
        [sys.executable, "-c", "print('hi')"],
        cwd=str(tmp_path),
        network="deny",
    )
    # The honest missing-dimension flag: filesystem is never contained in M1.
    assert result.achieved.filesystem_isolated is False
    # env scrub + home redirect always hold; they are honest, separate signals.
    assert result.achieved.env_scrubbed is True
    assert result.achieved.home_isolated is True
    # The over-claim is gone: even a fully env+network+home-hardened run stays
    # ``none`` because the filesystem is not contained.
    assert result.achieved.sandbox_tier == "none"


def test_fully_isolated_run_is_labeled_none_with_honest_booleans(tmp_path):
    """C1 (#184): on a host that CAN deny the network, a run with all three
    lower flags true still reports ``sandbox_tier == "none"`` (never ``jail``),
    while the real booleans (``env_scrubbed`` / ``network_denied`` True,
    ``filesystem_isolated`` False) stay honest and separate. (This test
    previously asserted ``jail`` — the over-claim C1 removes.)"""
    result = run_isolated(
        [sys.executable, "-c", "print('hi')"],
        cwd=str(tmp_path),
        network="deny",
    )
    if not result.achieved.network_denied:
        pytest.skip(
            "network deny unavailable on this platform; cannot exercise the "
            "all-three-flags-true case"
        )
    assert result.achieved.env_scrubbed is True
    assert result.achieved.home_isolated is True
    assert result.achieved.network_denied is True
    # No filesystem containment -> honest tier is none, never jail.
    assert result.achieved.filesystem_isolated is False
    assert result.achieved.sandbox_tier == "none"


def test_tier_never_container_or_microvm(tmp_path):
    for network in ("deny", "allow"):
        result = run_isolated(
            [sys.executable, "-c", "print('x')"],
            cwd=str(tmp_path),
            network=network,
        )
        assert result.achieved.sandbox_tier in {"none", "jail"}


def test_report_is_frozen_dataclass(tmp_path):
    result = run_isolated(
        [sys.executable, "-c", "print('x')"],
        cwd=str(tmp_path),
        network="allow",
    )
    assert isinstance(result.achieved, IsolationReport)
    with pytest.raises(Exception):
        result.achieved.sandbox_tier = "microvm"  # frozen


# --- home isolation -------------------------------------------------------


def test_child_home_is_throwaway(tmp_path):
    real_home = os.path.expanduser("~")
    result = run_isolated(
        [sys.executable, "-c", "import os; print('HOME=' + os.path.expanduser('~'))"],
        cwd=str(tmp_path),
        network="allow",
    )
    assert result.returncode == 0, result.stderr
    assert "HOME=" in result.stdout
    child_home = result.stdout.split("HOME=", 1)[1].splitlines()[0].strip()
    assert child_home != real_home
    assert result.achieved.home_isolated is True


def test_explicit_home_is_used(tmp_path):
    explicit = tmp_path / "explicit-home"
    result = run_isolated(
        [sys.executable, "-c", "import os; print(os.path.expanduser('~'))"],
        cwd=str(tmp_path),
        home=str(explicit),
        network="allow",
    )
    assert result.returncode == 0, result.stderr
    assert str(explicit) in result.stdout
    assert result.achieved.home_isolated is True


# --- timeout --------------------------------------------------------------


def test_timeout_is_reported(tmp_path):
    result = run_isolated(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        network="allow",
        timeout=1.0,
    )
    assert result.timed_out is True
    assert result.returncode != 0
