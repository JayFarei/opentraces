"""Local sshd fixture — spawn a non-root OpenSSH `sshd` on a high port.

Lets the otbox Tier 1 pytest verify the SSH/rsync code paths without
requiring a developer's tailnet to be reachable from the test
environment, and without touching macOS's system-level Remote Login.

Usage::

    sshd = LocalSshd.start()
    target, key_path = sshd.target, sshd.client_key   # user@127.0.0.1, ~/.ssh/...
    # use as OT_OTBOX_SSH_TARGET=<target> OT_OTBOX_SSH_KEY=<key_path>
    sshd.stop()
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


SSHD_PATHS = ("/usr/sbin/sshd", "/usr/bin/sshd")


def _find_sshd() -> str | None:
    for p in SSHD_PATHS:
        if Path(p).exists():
            return p
    return shutil.which("sshd")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LocalSshd:
    tmp: Path
    port: int
    pid: int
    target: str
    client_key: Path
    proc: subprocess.Popen

    def stop(self) -> None:
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        except Exception:  # noqa: BLE001
            pass
        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)


def start() -> LocalSshd | None:
    """Return a running LocalSshd, or None if the platform can't host one."""
    sshd = _find_sshd()
    if sshd is None:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="otbox-sshd-"))
    host_key = tmp / "host_key"
    client_key = tmp / "client_key"
    authorized = tmp / "authorized_keys"
    cfg = tmp / "sshd_config"
    pidfile = tmp / "sshd.pid"

    for path, comment in ((host_key, "otbox host"), (client_key, "otbox client")):
        subprocess.run(
            ["ssh-keygen", "-q", "-N", "", "-t", "ed25519", "-f", str(path),
             "-C", comment],
            check=True, capture_output=True,
        )
    shutil.copy(f"{client_key}.pub", authorized)
    for p in (host_key, authorized):
        p.chmod(0o600)

    port = _free_port()
    cfg.write_text(
        f"Port {port}\n"
        f"ListenAddress 127.0.0.1\n"
        f"HostKey {host_key}\n"
        f"PidFile {pidfile}\n"
        f"AuthorizedKeysFile {authorized}\n"
        f"PasswordAuthentication no\n"
        f"ChallengeResponseAuthentication no\n"
        f"UsePAM no\n"
        f"StrictModes no\n"
        f"PermitRootLogin no\n"
        f"PrintMotd no\n"
        f"AcceptEnv *\n"
        f"Subsystem sftp internal-sftp\n"
    )

    proc = subprocess.Popen(
        [sshd, "-f", str(cfg), "-h", str(host_key), "-D", "-e"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    # Poll for listen-ready.
    deadline = time.monotonic() + 5
    ready = False
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.1)
    if not ready:
        proc.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
        return None

    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "otbox"
    return LocalSshd(
        tmp=tmp, port=port, pid=proc.pid,
        target=f"{user}@127.0.0.1",
        client_key=client_key, proc=proc,
    )
