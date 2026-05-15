"""DockerDriver — opt-in stronger-isolation Tier 0 substrate.

Same box layout as ``LocalDriver``, but the opentraces CLI runs inside a
long-lived container instead of as a host subprocess. The box root is
bind-mounted into the container, so the portable workspace-archive
snapshot path (``otbox.snapshot``) keeps working unchanged.

Honesty note: this driver needs a Linux image with opentraces already
installed (the host ``.venv`` holds macOS binaries that will not run in
a Linux container, and building such an image needs network — which the
autonomous Tier 0 contract forbids). So ``provision`` requires a
prebuilt image and fails fast with a clear instruction otherwise. The
``local`` driver remains the offline-verified default.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from ..env import isolated_env
from .base import Driver, ExecResult, timed

DEFAULT_IMAGE = "otbox-runtime:latest"
# Where the box root is mounted inside the container.
CONTAINER_BOX_ROOT = "/otbox/box"


class DockerUnavailable(RuntimeError):
    pass


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _image_exists(image: str) -> bool:
    proc = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


class DockerDriver(Driver):
    name = "docker"
    tier = 0

    def __init__(self) -> None:
        self.image = os.environ.get("OTBOX_DOCKER_IMAGE", DEFAULT_IMAGE)

    # ----- lifecycle ------------------------------------------------------
    def _container_name(self, box) -> str:
        return f"otbox-{box.box_id}"

    def provision(self, box) -> None:
        if not _docker_available():
            raise DockerUnavailable(
                "docker CLI not found — use `--driver local` (the default) or install Docker"
            )
        if not _image_exists(self.image):
            raise DockerUnavailable(
                f"docker image {self.image!r} not found. The docker driver needs a "
                "prebuilt Linux image with opentraces installed; build it once with "
                "`otbox image build` (needs network), then retry. The `local` driver "
                "needs none of this and is the default."
            )
        # Same on-disk layout as LocalDriver — the box root is the mount source.
        for path in (
            box.root,
            box.home,
            box.opentraces_dir,
            box.project,
            box.fake_remote,
            box.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)
        gitconfig = box.home / ".gitconfig"
        if not gitconfig.exists():
            gitconfig.write_text(
                "[user]\n\tname = otbox\n\temail = otbox@opentraces.local\n"
                "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n"
            )

        name = self._container_name(box)
        # Remove any stale container with this name first (idempotent provision).
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
        proc = subprocess.run(
            [
                "docker", "run", "-d", "--name", name,
                "-v", f"{box.root}:{CONTAINER_BOX_ROOT}",
                "-w", f"{CONTAINER_BOX_ROOT}/project",
                self.image,
                "sleep", "infinity",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise DockerUnavailable(f"docker run failed: {proc.stderr.strip()}")
        box.notes["docker_container"] = name

    def exec(
        self,
        box,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env_extra: dict | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        argv = [str(a) for a in argv]
        name = box.notes.get("docker_container", self._container_name(box))
        # Translate host paths in the isolated env to their in-container mounts.
        host_env = isolated_env(box, env_extra)
        container_env: dict[str, str] = {}
        host_root = str(box.root)
        for key in ("HOME", "OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", "GIT_CONFIG_GLOBAL"):
            val = host_env.get(key, "")
            if val.startswith(host_root):
                container_env[key] = val.replace(host_root, CONTAINER_BOX_ROOT, 1)
        for key in ("HF_HUB_DISABLE_IMPLICIT_TOKEN", "GIT_PAGER", "PAGER", "OTBOX_BOX_ID"):
            if key in host_env:
                container_env[key] = host_env[key]

        run_cwd = (
            str(cwd).replace(host_root, CONTAINER_BOX_ROOT, 1)
            if cwd is not None
            else f"{CONTAINER_BOX_ROOT}/project"
        )
        docker_cmd = ["docker", "exec", "-w", run_cwd]
        for key, val in container_env.items():
            docker_cmd += ["-e", f"{key}={val}"]
        docker_cmd += [name, *argv]

        timed_out = False
        with timed() as clock:
            try:
                proc = subprocess.run(
                    docker_cmd, capture_output=True, text=True, timeout=timeout
                )
                returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
            except subprocess.TimeoutExpired:
                timed_out = True
                returncode, stdout, stderr = 124, "", f"[otbox] docker exec timed out after {timeout}s"
        return ExecResult(
            argv=argv,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_s=clock.duration,
            cwd=run_cwd,
            timed_out=timed_out,
        )

    def teardown(self, box) -> None:
        name = box.notes.get("docker_container", self._container_name(box))
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
        if box.root.exists():
            shutil.rmtree(box.root)
