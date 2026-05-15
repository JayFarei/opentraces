"""RemoteDriver — Tier 1 substrate over SSH (Tailscale-local, plan 061).

Same Driver protocol as Tier 0, but every operation goes over an SSH
connection to a leased target. Connection reuse via OpenSSH
ControlMaster makes the multi-step journey runner cheap; rsync handles
workspace sync; tar + scp handle snapshots. No cloud APIs, no broker —
just SSH (over whatever network, typically Tailscale).

Hard-gated by ``OT_OTBOX_TIER1=1``; target supplied by
``OT_OTBOX_SSH_TARGET`` (``user@host`` or any string SSH understands).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from ..env import REPO_ROOT, isolated_env
from .base import Driver, ExecResult, timed

TIER1_FLAG = "OT_OTBOX_TIER1"
SSH_TARGET_ENV = "OT_OTBOX_SSH_TARGET"
SSH_PORT_ENV = "OT_OTBOX_SSH_PORT"
SSH_KEY_ENV = "OT_OTBOX_SSH_KEY"
REMOTE_PYTHON_ENV = "OT_OTBOX_REMOTE_PYTHON"
REMOTE_VENV_ENV = "OT_OTBOX_REMOTE_VENV"
REMOTE_ROOT_TPL = "~/.otbox-remote"

# Python interpreters to probe on the remote (highest version first).
_PYTHON_PROBES = (
    "python3.14", "python3.13", "python3.12", "python3.11", "python3.10", "python3",
)


class Tier1Disabled(RuntimeError):
    pass


def tier1_enabled() -> bool:
    return os.environ.get(TIER1_FLAG) == "1"


class RemoteDriver(Driver):
    name = "remote"
    tier = 1

    def __init__(self) -> None:
        self.target = os.environ.get(SSH_TARGET_ENV)
        self.port = os.environ.get(SSH_PORT_ENV)
        self.identity = os.environ.get(SSH_KEY_ENV)
        self.preset_venv = os.environ.get(REMOTE_VENV_ENV)
        self.preset_python = os.environ.get(REMOTE_PYTHON_ENV)

    # ----- gating ---------------------------------------------------------
    def _require(self) -> str:
        if not tier1_enabled():
            raise Tier1Disabled(
                f"Tier 1 is opt-in and never runs in default CI — set {TIER1_FLAG}=1 "
                "to enable the remote driver"
            )
        if not self.target:
            raise Tier1Disabled(
                f"no remote target — set {SSH_TARGET_ENV} (e.g. user@host)"
            )
        return self.target

    # ----- SSH plumbing ---------------------------------------------------
    def _control_path(self, box) -> str:
        # macOS unix sockets cap at 104 chars, so keep the path short by
        # hashing the box id into /tmp instead of nesting under .otbox/boxes/.
        return f"/tmp/ob-{box.box_id}.sock"

    def _ssh_base(self, box) -> list[str]:
        argv = ["ssh", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes",
                "-o", f"ControlPath={self._control_path(box)}"]
        if self.port:
            argv += ["-p", self.port]
        if self.identity:
            argv += ["-i", self.identity]
        return argv

    def _rsync_base(self, box) -> list[str]:
        # rsync over the same ControlMaster channel for speed.
        ssh = "ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes" \
              f" -o ControlPath={shlex.quote(self._control_path(box))}"
        if self.port:
            ssh += f" -p {self.port}"
        if self.identity:
            ssh += f" -i {shlex.quote(self.identity)}"
        return ["rsync", "-az", "--delete", "-e", ssh]

    def _start_master(self, box, target: str) -> None:
        cp = self._control_path(box)
        if Path(cp).exists():  # already up
            check = subprocess.run(self._ssh_base(box) + ["-O", "check", target],
                                   capture_output=True, text=True)
            if check.returncode == 0:
                return
            Path(cp).unlink(missing_ok=True)
        # Spawn master in background; -fN means no command, daemonize.
        argv = self._ssh_base(box) + [
            "-o", "ControlMaster=yes",
            "-o", "ControlPersist=10m",
            "-fN", target,
        ]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            raise Tier1Disabled(
                f"SSH ControlMaster failed for {target}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )

    def _stop_master(self, box, target: str) -> None:
        cp = self._control_path(box)
        if not Path(cp).exists():
            return
        subprocess.run(self._ssh_base(box) + ["-O", "exit", target],
                       capture_output=True, text=True)
        Path(cp).unlink(missing_ok=True)

    def _ssh(self, box, target: str, cmd: str, *, timeout: float | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._ssh_base(box) + [target, cmd],
            capture_output=True, text=True, timeout=timeout,
        )

    # ----- remote layout --------------------------------------------------
    def _remote_root(self, box) -> str:
        return box.notes.get("remote_root") or f"{REMOTE_ROOT_TPL}/{box.box_id}"

    def _remote_source(self, box) -> str:
        return f"{self._remote_root(box)}/source"

    def _remote_python(self, box) -> str:
        if self.preset_venv:
            return f"{self.preset_venv}/bin/python"
        return f"{self._remote_root(box)}/venv/bin/python"

    # ----- prerequisites --------------------------------------------------
    def _resolve_remote_python(self, box, target: str) -> str:
        """Pick a Python 3.10+ on the remote, or refuse."""
        if self.preset_python:
            return self.preset_python
        # Probe known names; print version and minor for parsing.
        probe = " ; ".join(
            f'command -v {p} >/dev/null && {p} -c "import sys; print({p!r}, sys.version_info[:2])" 2>/dev/null'
            for p in _PYTHON_PROBES
        )
        proc = self._ssh(box, target, probe, timeout=10)
        if proc.returncode != 0:
            raise Tier1Disabled(
                f"prerequisite: could not probe Python on {target}:\n{proc.stderr}"
            )
        best: tuple[str, tuple[int, int]] | None = None
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("'"):
                continue
            try:
                # Lines look like: 'python3.14' (3, 14)
                name = line.split(" ", 1)[0].strip("'")
                rest = line.split(" ", 1)[1].strip()
                tup = tuple(int(x.strip()) for x in rest.strip("()").split(","))
                if tup[:2] >= (3, 10) and (best is None or tup[:2] > best[1]):
                    best = (name, tup[:2])  # type: ignore[assignment]
            except Exception:  # noqa: BLE001
                continue
        if best is None:
            raise Tier1Disabled(
                f"prerequisite: python3.10+ not found on {target}. "
                "Install python3.10+ on the remote and retry, or set "
                f"{REMOTE_PYTHON_ENV} to an explicit interpreter path."
            )
        return best[0]

    # ----- lifecycle ------------------------------------------------------
    def provision(self, box) -> None:
        target = self._require()
        # Laptop-side scaffold (mirrors LocalDriver).
        for path in (box.root, box.logs):
            path.mkdir(parents=True, exist_ok=True)

        # Bring up the long-lived SSH control channel.
        self._start_master(box, target)

        # Resolve `~` once — shlex.quote() makes ~ literal otherwise, and we
        # need every later cd / mkdir / rsync to use absolute paths.
        home_probe = self._ssh(box, target, "printf %s \"$HOME\"", timeout=10)
        if home_probe.returncode != 0 or not home_probe.stdout.strip():
            self._stop_master(box, target)
            raise Tier1Disabled(
                f"could not resolve $HOME on {target}: {home_probe.stderr.strip()}"
            )
        remote_home = home_probe.stdout.strip()
        box.notes["remote_home"] = remote_home
        box.notes["remote_root"] = f"{remote_home}/.otbox-remote/{box.box_id}"

        # Refuse-not-install: prove a usable Python exists on the remote.
        remote_python = self._resolve_remote_python(box, target)

        remote = self._remote_root(box)
        layout = " ".join(f"{remote}/{sub}" for sub in (
            "source", "home/.opentraces", "project", "fake-remote", "logs",
        ))
        # Create the per-lease layout + the box-local gitconfig + venv.
        gitconfig = (
            "[user]\\n\\tname = otbox\\n\\temail = otbox@opentraces.local\\n"
            "[init]\\n\\tdefaultBranch = main\\n"
            "[commit]\\n\\tgpgsign = false\\n"
        )
        venv_step = (
            ""
            if self.preset_venv
            else f"{remote_python} -m venv {remote}/venv && "
        )
        bootstrap = (
            f"set -e; mkdir -p {layout} && "
            f"printf '%b' {shlex.quote(gitconfig)} > {remote}/home/.gitconfig && "
            f"{venv_step}"
            f"echo OK"
        )
        proc = self._ssh(box, target, bootstrap, timeout=120)
        if proc.returncode != 0:
            self._stop_master(box, target)
            raise Tier1Disabled(
                f"remote provision failed on {target}:\n{proc.stderr or proc.stdout}"
            )

        box.notes["remote_target"] = target
        box.notes["remote_root"] = remote
        box.notes["remote_python"] = remote_python
        box.notes["remote_venv"] = self.preset_venv or f"{remote}/venv"
        box.notes["preset_venv"] = bool(self.preset_venv)

    def teardown(self, box) -> None:
        target = box.notes.get("remote_target") or self.target
        remote = self._remote_root(box)
        if target and tier1_enabled():
            self._ssh(box, target, f"rm -rf {shlex.quote(remote)}", timeout=30)
            self._stop_master(box, target)
        if box.root.exists():
            shutil.rmtree(box.root)

    # ----- exec -----------------------------------------------------------
    def exec(
        self,
        box,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env_extra: dict | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        target = box.notes.get("remote_target") or self._require()
        argv = [str(a) for a in argv]
        remote = self._remote_root(box)
        # Default cwd: the seeded project on the remote.
        run_cwd = str(cwd) if cwd is not None else f"{remote}/project"

        # Translate isolated env to remote paths.
        host_env = isolated_env(box, env_extra)
        host_root = str(box.root)
        remote_env: dict[str, str] = {}
        for key in ("HOME", "OPENTRACES_PLAN058_FAKE_REMOTE_ROOT",
                    "GIT_CONFIG_GLOBAL"):
            val = host_env.get(key, "")
            if val.startswith(host_root):
                remote_env[key] = val.replace(host_root, remote, 1)
        for key in ("HF_HUB_DISABLE_IMPLICIT_TOKEN", "GIT_PAGER", "PAGER",
                    "OTBOX_BOX_ID"):
            if key in host_env:
                remote_env[key] = host_env[key]
        env_prefix = " ".join(
            f"{k}={shlex.quote(v)}" for k, v in sorted(remote_env.items())
        )
        quoted = " ".join(shlex.quote(a) for a in argv)
        remote_cmd = (
            f"cd {shlex.quote(run_cwd)} && env {env_prefix} {quoted}"
        )

        timed_out = False
        with timed() as clock:
            try:
                proc = self._ssh(box, target, remote_cmd, timeout=timeout)
                returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
            except subprocess.TimeoutExpired:
                timed_out = True
                returncode, stdout, stderr = 124, "", f"[otbox] ssh exec timed out after {timeout}s"
        return ExecResult(
            argv=argv, returncode=returncode, stdout=stdout, stderr=stderr,
            duration_s=clock.duration, cwd=run_cwd, timed_out=timed_out,
        )

    # ----- workspace sync (R2) -------------------------------------------
    _DEFAULT_EXCLUDES = (
        ".venv/", ".venv-*/", ".otbox/", "__pycache__/", "*.pyc",
        "node_modules/", ".git/objects/pack/", ".pytest_cache/",
        ".ruff_cache/", ".vercel/", ".next/", "dist/", "build/",
        "*.egg-info/", ".DS_Store",
    )

    def sync(self, box, *, full_resync: bool = False) -> ExecResult:
        target = box.notes.get("remote_target") or self._require()
        remote = self._remote_root(box)
        remote_source = f"{self._remote_source(box)}/"

        if full_resync:
            self._ssh(box, target, f"rm -rf {shlex.quote(remote_source)}", timeout=30)
            self._ssh(box, target, f"mkdir -p {shlex.quote(remote_source)}", timeout=15)

        # Excludes: defaults + any .otboxignore at the repo root.
        excludes = list(self._DEFAULT_EXCLUDES)
        ignore = REPO_ROOT / ".otboxignore"
        if ignore.exists():
            excludes.extend(
                line.strip() for line in ignore.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            )
        rsync_argv = self._rsync_base(box)
        for pat in excludes:
            rsync_argv += ["--exclude", pat]
        rsync_argv += [f"{REPO_ROOT}/", f"{target}:{remote_source}"]

        with timed() as clock:
            proc = subprocess.run(rsync_argv, capture_output=True, text=True, timeout=600)

        if proc.returncode != 0:
            return ExecResult(
                argv=rsync_argv, returncode=proc.returncode,
                stdout=proc.stdout, stderr=proc.stderr,
                duration_s=clock.duration, cwd=str(REPO_ROOT), timed_out=False,
            )

        # First-time install (skipped when preset_venv is in use — same
        # machine assumed to have opentraces deps already).
        if not self.preset_venv and not box.notes.get("synced_installed"):
            install = (
                f"{shlex.quote(box.notes['remote_venv'])}/bin/pip install --quiet "
                f"-e {shlex.quote(remote)}/source/packages/opentraces-schema && "
                f"{shlex.quote(box.notes['remote_venv'])}/bin/pip install --quiet "
                f"-e {shlex.quote(remote)}/source"
            )
            install_proc = self._ssh(box, target, install, timeout=900)
            if install_proc.returncode != 0:
                return ExecResult(
                    argv=["pip", "install"], returncode=install_proc.returncode,
                    stdout=install_proc.stdout, stderr=install_proc.stderr,
                    duration_s=clock.duration, cwd=remote, timed_out=False,
                )
            box.notes["synced_installed"] = True
            box.save()

        return ExecResult(
            argv=rsync_argv, returncode=0, stdout=proc.stdout, stderr=proc.stderr,
            duration_s=clock.duration, cwd=str(REPO_ROOT), timed_out=False,
        )

    # ----- background services (popen) -----------------------------------
    def popen(
        self,
        box,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env_extra: dict | None = None,
    ) -> subprocess.Popen:
        """Long-running remote command via ssh (used by `service` step)."""
        target = box.notes.get("remote_target") or self._require()
        argv = [str(a) for a in argv]
        remote = self._remote_root(box)
        run_cwd = str(cwd) if cwd is not None else f"{remote}/project"
        host_env = isolated_env(box, env_extra)
        host_root = str(box.root)
        remote_env = {}
        for k, v in host_env.items():
            if k in ("HOME", "OPENTRACES_PLAN058_FAKE_REMOTE_ROOT",
                     "GIT_CONFIG_GLOBAL") and v.startswith(host_root):
                remote_env[k] = v.replace(host_root, remote, 1)
            elif k in ("HF_HUB_DISABLE_IMPLICIT_TOKEN", "GIT_PAGER",
                       "PAGER", "OTBOX_BOX_ID"):
                remote_env[k] = v
        env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(remote_env.items()))
        quoted = " ".join(shlex.quote(a) for a in argv)
        remote_cmd = f"cd {shlex.quote(run_cwd)} && env {env_prefix} {quoted}"
        return subprocess.Popen(
            self._ssh_base(box) + [target, remote_cmd],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    # ----- snapshot / restore (R5, hard interchange invariant) -----------
    def snapshot(self, box, name: str, *, overwrite: bool = False):
        """Tar the remote box and rsync-pull the archive back to the laptop.

        Archive format is byte-for-byte the workspace-archive that the
        ``local`` driver produces, so a Tier 1 snapshot restores to a
        Tier 0 box and vice versa (spec hard invariant).
        """
        import json as _json
        import time as _time

        from ..env import utc_now
        from ..snapshot import SnapshotError, SnapshotInfo, _archive_path, _meta_path  # noqa: PLC2701

        target = box.notes.get("remote_target") or self._require()
        remote_root = self._remote_root(box)
        archive = _archive_path(name)
        if archive.exists() and not overwrite:
            raise SnapshotError(
                f"snapshot {name!r} already exists; pass overwrite=True to replace it"
            )
        archive.parent.mkdir(parents=True, exist_ok=True)

        # Stream the tar straight from the remote into a local .partial file.
        # ``source/`` and ``venv/`` get rebuilt by provision + sync on
        # restore, so excluding them keeps archives small and matches
        # the Tier 0 archive contents exactly (interchange invariant).
        tar_cmd = (
            f"tar -C {shlex.quote(remote_root)} -czf - "
            "--exclude=./ssh-control --exclude=./.snapshot.tar.gz "
            "--exclude=./source --exclude=./venv ."
        )
        # Hard invariant: ssh-control sockets etc. must never end up in
        # the archive (Tier 0 archives don't have them either).
        ssh_argv = self._ssh_base(box) + [target, tar_cmd]
        tmp = archive.with_suffix(".tar.gz.partial")
        start = _time.monotonic()
        with tmp.open("wb") as f:
            proc = subprocess.run(ssh_argv, stdout=f, stderr=subprocess.PIPE, timeout=600)
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise SnapshotError(
                f"remote snapshot tar failed rc={proc.returncode}: "
                f"{proc.stderr.decode(errors='replace').strip()[:300]}"
            )
        tmp.replace(archive)

        info = SnapshotInfo(
            name=name, archive=archive,
            origin_box_id=box.box_id, origin_root=remote_root,
            seed=box.seed, driver=box.driver, created=utc_now(),
            size_bytes=archive.stat().st_size,
        )
        _meta_path(name).write_text(_json.dumps(info.to_dict(), indent=2, sort_keys=True) + "\n")
        # Stash duration for the caller.
        box.notes["last_snapshot_seconds"] = round(_time.monotonic() - start, 3)
        return info

    def restore(self, name: str, *, box_id: str | None = None):
        """Provision a fresh remote box and unpack the archive into it."""
        import time as _time

        from ..env import Box as _Box, new_box_id
        from ..snapshot import load_snapshot, SnapshotError

        info = load_snapshot(name)
        new_box = _Box(
            box_id=box_id or new_box_id(),
            driver=self.name, seed=info.seed,
            status="restored", restored_from=name,
        )
        if new_box.root.exists():
            raise SnapshotError(f"box {new_box.box_id} already exists locally")

        # provision creates the remote layout (mkdir, venv, etc.)
        self.provision(new_box)
        target = new_box.notes["remote_target"]
        new_remote = self._remote_root(new_box)

        start = _time.monotonic()
        # rsync the archive up.
        scp_argv = self._rsync_base(new_box) + [
            str(info.archive), f"{target}:{new_remote}/.snapshot.tar.gz",
        ]
        proc = subprocess.run(scp_argv, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise SnapshotError(f"archive upload failed: {proc.stderr.strip()}")

        # Untar into the remote box (overwrites the empty provision layout).
        untar = (
            f"tar -C {shlex.quote(new_remote)} -xzf "
            f"{shlex.quote(new_remote)}/.snapshot.tar.gz && "
            f"rm -f {shlex.quote(new_remote)}/.snapshot.tar.gz"
        )
        untar_proc = self._ssh(new_box, target, untar, timeout=300)
        if untar_proc.returncode != 0:
            raise SnapshotError(f"untar failed: {untar_proc.stderr.strip()}")

        # Path rewrite: old root -> new remote root. Same logic as Tier 0
        # but executed on the remote via a tiny python one-liner.
        rewrites = self._rewrite_remote_paths(new_box, info.origin_root, new_remote)
        new_box.save()
        # Snapshots intentionally exclude ``source/`` + ``venv/`` (they
        # rebuild on each lease), so sync the laptop's working tree into
        # the freshly restored box so the CLI is runnable immediately.
        sync = self.sync(new_box)
        return new_box, {
            "snapshot": name,
            "origin_box_id": info.origin_box_id,
            "origin_root": info.origin_root,
            "paths_rewritten": rewrites,
            "restore_duration_s": round(_time.monotonic() - start, 3),
            "post_restore_sync": {"rc": sync.returncode, "dur_s": sync.duration_s},
        }

    def _rewrite_remote_paths(self, box, old_root: str, new_root: str) -> int:
        """Replay snapshot.py's absolute-path rewrite, but on the remote."""
        target = box.notes["remote_target"]
        python = (
            box.notes.get("remote_python")
            or self.preset_python
            or "python3"
        )
        script = (
            "import os, sys\n"
            f"old, new = {old_root!r}, {new_root!r}\n"
            "roots = [os.path.join(new, 'home', '.opentraces')]\n"
            "marker = os.path.join(new, 'project', '.opentraces.json')\n"
            "files = []\n"
            "for r in roots:\n"
            "  for dp, _, fn in os.walk(r) if os.path.isdir(r) else []:\n"
            "    files.extend(os.path.join(dp, f) for f in fn)\n"
            "if os.path.isfile(marker): files.append(marker)\n"
            "n = 0\n"
            "for p in files:\n"
            "  try: t = open(p, 'r', encoding='utf-8').read()\n"
            "  except Exception: continue\n"
            "  if old in t:\n"
            "    open(p, 'w', encoding='utf-8').write(t.replace(old, new))\n"
            "    n += 1\n"
            "print(n)\n"
        )
        cmd = f"{shlex.quote(python)} -c {shlex.quote(script)}"
        proc = self._ssh(box, target, cmd, timeout=60)
        try:
            return int(proc.stdout.strip())
        except ValueError:
            return 0

    # ----- box-relative filesystem (Tier 1 overrides) ---------------------
    def paths(self, box) -> dict:
        remote = self._remote_root(box)
        return {
            "root": remote,
            "project": f"{remote}/project",
            "home": f"{remote}/home",
            "opentraces_dir": f"{remote}/home/.opentraces",
            "fake_remote": f"{remote}/fake-remote",
        }

    def put_text(self, box, path: str, content: str) -> None:
        target = box.notes.get("remote_target") or self._require()
        parent = path.rsplit("/", 1)[0]
        # mkdir parent + cat via heredoc; quote a marker that won't appear
        # in content (use base64 to bypass shell quoting entirely).
        import base64
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        cmd = (
            f"mkdir -p {shlex.quote(parent)} && "
            f"echo {b64} | base64 -d > {shlex.quote(path)}"
        )
        proc = self._ssh(box, target, cmd, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"put_text {path}: {proc.stderr.strip()}")

    def mkdir(self, box, path: str, *, parents: bool = True) -> None:
        target = box.notes.get("remote_target") or self._require()
        flag = "-p" if parents else ""
        proc = self._ssh(box, target, f"mkdir {flag} {shlex.quote(path)}", timeout=15)
        if proc.returncode != 0:
            raise RuntimeError(f"mkdir {path}: {proc.stderr.strip()}")

    def path_exists(self, box, path: str) -> bool:
        target = box.notes.get("remote_target") or self._require()
        proc = self._ssh(box, target, f"test -e {shlex.quote(path)}", timeout=15)
        return proc.returncode == 0

    def count_files(self, box, root: str, pattern: str = "**/*") -> int:
        target = box.notes.get("remote_target") or self._require()
        py = box.notes.get("remote_python") or "python3"
        script = (
            "import pathlib,sys\n"
            f"p = pathlib.Path({root!r})\n"
            "n = 0 if not p.exists() else sum(1 for f in p.glob("
            f"{pattern!r}) if f.is_file())\n"
            "print(n)\n"
        )
        proc = self._ssh(box, target,
                         f"{shlex.quote(py)} -c {shlex.quote(script)}",
                         timeout=20)
        try:
            return int(proc.stdout.strip())
        except ValueError:
            return 0

    def glob(self, box, pattern: str) -> list[str]:
        target = box.notes.get("remote_target") or self._require()
        # Use a tiny python one-liner for predictable glob semantics.
        py = box.notes.get("remote_python") or "python3"
        script = f"import glob,sys\nfor p in sorted(glob.glob({pattern!r})): print(p)"
        proc = self._ssh(box, target,
                         f"{shlex.quote(py)} -c {shlex.quote(script)}",
                         timeout=15)
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    # ----- cli_argv (override Driver default) -----------------------------
    def cli_argv(self, box) -> list[str]:
        """argv that runs the opentraces CLI inside the leased box.

        Uses the remote venv's Python; reads opentraces from the synced
        source via PYTHONPATH so we never have to rebuild the editable
        install on every sync.
        """
        remote = self._remote_root(box)
        venv = box.notes.get("remote_venv") or f"{remote}/venv"
        py = f"{venv}/bin/python"
        # Inline bootstrap that prepends the synced source to sys.path
        # then dispatches to the CLI's main().
        bootstrap = (
            "import sys; "
            f"sys.path[:0] = ['{remote}/source/src', "
            f"'{remote}/source/packages/opentraces-schema/src']; "
            "from opentraces.cli import main; "
            "raise SystemExit(main(prog_name='otbox-cli'))"
        )
        return [py, "-c", bootstrap]
