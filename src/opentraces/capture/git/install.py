"""Install / remove / verify the opentraces git post-commit hook.

Minimal installer that doesn't depend on plan 042's unified installer
framework: just writes a small .git/hooks/opentraces-post-commit
script owned by us and chains it from .git/hooks/post-commit via a
fenced block we can detect and remove cleanly.

Plan 041 R21.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ...core.integration_versions import (
    current_cli_version,
    extract_version_stamp,
    version_drift,
)
from ...enrichment.git.notes_store import NOTES_REF
from .._base import HookInstallResult

HOOK_FILENAME = "opentraces-post-commit"
CHAIN_BEGIN = "# >>> opentraces post-commit chain >>>"
CHAIN_END = "# <<< opentraces post-commit chain <<<"
NOTES_REFSPEC = f"+{NOTES_REF}:{NOTES_REF}"

# The shim runs inside git's post-commit environment, which strips PATH
# down to a minimal set. A bare `opentraces` call resolves to nothing in
# that env, so the hook silently no-ops for every commit. Pin the python
# interpreter that installed opentraces and invoke via `-m opentraces`
# so the hook keeps working regardless of the shell's PATH.
OWNED_HOOK_TEMPLATE = """\
#!/usr/bin/env sh
# Installed by opentraces. Plan 041. Safe to delete.
# opentraces-version: {version}
# Runs the post-commit correlator and appends notes on refs/notes/opentraces.
# Never blocks git commit: any failure exits 0.
set +e
"{python}" -m opentraces _run-post-commit-hook "$(pwd)" >/dev/null 2>&1 \\
  || opentraces _run-post-commit-hook "$(pwd)" >/dev/null 2>&1 \\
  || true
exit 0
"""


def _owned_hook_content() -> str:
    """Render the shim with the current Python interpreter path baked in."""
    python = sys.executable or shutil.which("python3") or "python3"
    return OWNED_HOOK_TEMPLATE.format(python=python, version=current_cli_version())


# Back-compat for any caller that imported the constant directly.
OWNED_HOOK_CONTENT = _owned_hook_content()


def _git_dir(repo: Path) -> Path | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(repo), text=True,
            stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
    except Exception:
        return None
    p = Path(out)
    if not p.is_absolute():
        p = (repo / p).resolve()
    return p


def _chmod_x(path: Path) -> None:
    st = path.stat().st_mode
    path.chmod(st | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _ensure_notes_refspec(repo: Path) -> None:
    """Ensure exactly one notes fetch refspec entry (fixed-point;
    also converges legacy duplicates from pre-#59 installs)."""
    try:
        proc = subprocess.run(
            ["git", "config", "--get-all", "remote.origin.fetch"],
            cwd=str(repo), capture_output=True, text=True, timeout=5,
        )
        existing = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        count = existing.count(NOTES_REFSPEC)
        if count == 1:
            return
        if count == 0:
            subprocess.check_call(
                ["git", "config", "--add", "remote.origin.fetch", NOTES_REFSPEC],
                cwd=str(repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            # Legacy duplicated state: collapse all identical entries to one.
            subprocess.check_call(
                ["git", "config", "--fixed-value", "--replace-all",
                 "remote.origin.fetch", NOTES_REFSPEC, NOTES_REFSPEC],
                cwd=str(repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass  # no origin remote / read failure is fine, matches existing behavior


def install(repo: Path) -> bool:
    """Install the opentraces post-commit hook + notes refspec.

    Returns True on success. Idempotent: re-installing does not
    duplicate the chain block.
    """
    gdir = _git_dir(repo)
    if gdir is None:
        return False
    hooks_dir = gdir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    owned = hooks_dir / HOOK_FILENAME
    owned.write_text(_owned_hook_content())
    _chmod_x(owned)

    pc = hooks_dir / "post-commit"
    chain_block = (
        f"{CHAIN_BEGIN}\n"
        f'"$(dirname "$0")"/{HOOK_FILENAME} "$@"\n'
        f"{CHAIN_END}\n"
    )
    if pc.exists():
        existing = pc.read_text()
        if CHAIN_BEGIN in existing:
            # already installed
            pass
        else:
            new = existing.rstrip("\n") + "\n\n" + chain_block
            pc.write_text(new)
    else:
        pc.write_text("#!/usr/bin/env sh\n\n" + chain_block)
    _chmod_x(pc)

    # Fetch refspec: ensure notes come along on `git fetch`.
    _ensure_notes_refspec(repo)
    return True


def remove(repo: Path) -> bool:
    """Remove the opentraces hook and chain block. Idempotent."""
    gdir = _git_dir(repo)
    if gdir is None:
        return False
    hooks_dir = gdir / "hooks"
    owned = hooks_dir / HOOK_FILENAME
    if owned.exists():
        owned.unlink()
    pc = hooks_dir / "post-commit"
    if pc.exists():
        text = pc.read_text()
        if CHAIN_BEGIN in text and CHAIN_END in text:
            start = text.index(CHAIN_BEGIN)
            end = text.index(CHAIN_END) + len(CHAIN_END)
            cleaned = (text[:start].rstrip() + text[end:].lstrip()).strip()
            if cleaned and cleaned != "#!/usr/bin/env sh":
                pc.write_text(cleaned + "\n")
            else:
                pc.unlink()
    # Remove the notes refspec entry if it exists.
    try:
        subprocess.check_call(
            ["git", "config", "--unset-all", "remote.origin.fetch", NOTES_REFSPEC],
            cwd=str(repo),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    return True


def status(repo: Path) -> dict:
    """Return a dict describing installation state."""
    gdir = _git_dir(repo)
    if gdir is None:
        return {"installer": "git", "installed": False, "reason": "not a git repo"}
    hooks_dir = gdir / "hooks"
    owned = hooks_dir / HOOK_FILENAME
    pc = hooks_dir / "post-commit"
    has_owned = owned.exists()
    hook_text = ""
    if has_owned:
        try:
            hook_text = owned.read_text()
        except OSError:
            hook_text = ""
    has_chain = pc.exists() and CHAIN_BEGIN in pc.read_text()
    deployed_version = extract_version_stamp(hook_text)
    drift = version_drift(deployed_version) if has_owned and has_chain else []
    return {
        "installer": "git",
        "installed": has_owned and has_chain,
        "owned_hook_present": has_owned,
        "chain_present": has_chain,
        "hook_dir": str(hooks_dir),
        "deployed_version": deployed_version,
        "cli_version": current_cli_version(),
        "drift": drift,
    }


@dataclass
class GitHookInstaller:
    """HookInstaller protocol adapter for git post-commit."""

    installer_name: str = "git"
    repo: Path | None = None

    def _target(self) -> Path:
        return self.repo or Path.cwd()

    def plan(self) -> list[dict]:
        gdir = _git_dir(self._target())
        hook_dir = str(gdir / "hooks") if gdir else "<no-git-repo>"
        return [
            {
                "event": "post-commit",
                "source": "<generated>",
                "dest": f"{hook_dir}/{HOOK_FILENAME}",
            }
        ]

    def install(self) -> HookInstallResult:
        ok = install(self._target())
        st = status(self._target())
        return HookInstallResult(
            ok=ok,
            installed={"post-commit": st.get("hook_dir", "")} if ok else {},
            added=["post-commit"] if ok and st.get("installed") else [],
            notes=[] if ok else ["not a git repo or insufficient permissions"],
        )

    def remove(self) -> HookInstallResult:
        ok = remove(self._target())
        return HookInstallResult(ok=ok, removed=["post-commit"] if ok else [])

    def status(self) -> dict:
        return status(self._target())
