"""otbox environment model — state root, box layout, isolated env.

A *box* is one isolated opentraces world. Everything a box touches lives
under ``.otbox/boxes/<id>/`` so teardown is a single ``rmtree`` and the
developer's real ``~/.opentraces``, shell profile, and git config are
never mutated (spec R2 — zero host residue).
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# tests/otbox/env.py -> repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]

STATE_ROOT = REPO_ROOT / ".otbox"
BOXES_DIR = STATE_ROOT / "boxes"
SNAPSHOTS_DIR = STATE_ROOT / "snapshots"
ARTIFACTS_DIR = STATE_ROOT / "artifacts"
CURRENT_POINTER = STATE_ROOT / "current"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_box_id() -> str:
    return f"otb_{secrets.token_hex(4)}"


@dataclass
class Box:
    """One isolated opentraces world on a substrate driver."""

    box_id: str
    driver: str = "local"
    seed: str | None = None
    status: str = "provisioned"
    created: str = field(default_factory=utc_now)
    restored_from: str | None = None
    notes: dict = field(default_factory=dict)

    # ----- layout ---------------------------------------------------------
    @property
    def root(self) -> Path:
        return BOXES_DIR / self.box_id

    @property
    def home(self) -> Path:
        """Isolated HOME — ``~/.opentraces`` and ``~/.claude`` land here."""
        return self.root / "home"

    @property
    def project(self) -> Path:
        """The seeded git project the CLI runs against."""
        return self.root / "project"

    @property
    def fake_remote(self) -> Path:
        """Fake HuggingFace remote root (OPENTRACES_PLAN058_FAKE_REMOTE_ROOT)."""
        return self.root / "fake-remote"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    @property
    def opentraces_dir(self) -> Path:
        return self.home / ".opentraces"

    # ----- persistence ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "box_id": self.box_id,
            "driver": self.driver,
            "seed": self.seed,
            "status": self.status,
            "created": self.created,
            "restored_from": self.restored_from,
            "notes": self.notes,
        }

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")

    @classmethod
    def from_dict(cls, data: dict) -> "Box":
        return cls(
            box_id=data["box_id"],
            driver=data.get("driver", "local"),
            seed=data.get("seed"),
            status=data.get("status", "provisioned"),
            created=data.get("created", utc_now()),
            restored_from=data.get("restored_from"),
            notes=data.get("notes", {}),
        )

    @classmethod
    def load(cls, box_id: str) -> "Box":
        meta = BOXES_DIR / box_id / "meta.json"
        if not meta.exists():
            raise BoxNotFound(box_id)
        return cls.from_dict(json.loads(meta.read_text()))


class BoxNotFound(Exception):
    def __init__(self, box_id: str) -> None:
        super().__init__(f"no otbox box named {box_id!r}")
        self.box_id = box_id


# --------------------------------------------------------------------------
# State-root helpers
# --------------------------------------------------------------------------
def ensure_state_root() -> None:
    for d in (STATE_ROOT, BOXES_DIR, SNAPSHOTS_DIR, ARTIFACTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def list_box_ids() -> list[str]:
    if not BOXES_DIR.exists():
        return []
    return sorted(p.name for p in BOXES_DIR.iterdir() if (p / "meta.json").exists())


def list_snapshots() -> list[str]:
    if not SNAPSHOTS_DIR.exists():
        return []
    return sorted(p.stem[:-4] if p.name.endswith(".tar.gz") else p.stem
                  for p in SNAPSHOTS_DIR.glob("*.tar.gz"))


def get_current_box_id() -> str | None:
    if CURRENT_POINTER.exists():
        value = CURRENT_POINTER.read_text().strip()
        return value or None
    return None


def set_current_box_id(box_id: str | None) -> None:
    ensure_state_root()
    if box_id is None:
        if CURRENT_POINTER.exists():
            CURRENT_POINTER.unlink()
    else:
        CURRENT_POINTER.write_text(box_id + "\n")


def resolve_box(box_id: str | None) -> Box:
    """Resolve an explicit box id, or fall back to the current box."""
    if box_id is None:
        box_id = get_current_box_id()
    if box_id is None:
        raise BoxNotFound("<current>")
    return Box.load(box_id)


# --------------------------------------------------------------------------
# CLI entrypoint resolution (spec R4 — real entrypoint, never CliRunner)
# --------------------------------------------------------------------------
_CLI_BOOTSTRAP = 'from opentraces.cli import main; raise SystemExit(main(prog_name="otbox-cli"))'


def resolve_cli_argv() -> list[str]:
    """Return the base argv that invokes the real opentraces CLI.

    Order of preference, mirroring the ``otd`` shim's contract:
      1. ``$OT_CLI_BIN`` — explicit override (e.g. a release-candidate wheel).
      2. The repo-root ``otd`` shim, if present.
      3. The repo ``.venv`` python running the Click entrypoint in-process.
      4. ``python3`` as a last resort.
    """
    override = os.environ.get("OT_CLI_BIN")
    if override:
        return [override]
    otd = REPO_ROOT / "otd"
    if otd.exists() and os.access(otd, os.X_OK):
        return [str(otd)]
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python), "-c", _CLI_BOOTSTRAP]
    # Issue #42 phase 4 — last-resort bare ``python3``. If this interpreter
    # cannot ``import opentraces`` (worktree with no ``.venv`` and no
    # OT_CLI_BIN), every downstream checkpoint build dies with a
    # ModuleNotFoundError three subprocess layers deep that names neither
    # the cause nor the fix. Probe importability here and raise a clear,
    # actionable error at the resolution boundary instead.
    _assert_bare_python_has_cli_deps()
    return ["python3", "-c", _CLI_BOOTSTRAP]


def _assert_bare_python_has_cli_deps() -> None:
    """Fail loudly if the bare ``python3`` fallback can't import the CLI.

    Resolution-time guard (issue #42 phase 4): the checkpoint builders
    shell out to ``python3 -c "from opentraces.cli import main; ..."``.
    Without a repo ``.venv`` or an ``OT_CLI_BIN`` wrapper that pins
    ``PYTHONPATH`` at the worktree ``src``, that import explodes deep
    inside a subprocess. Surface the real remedy here.
    """
    import shutil
    import subprocess

    py = shutil.which("python3") or "python3"
    try:
        probe = subprocess.run(
            [py, "-c", "import opentraces.cli"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        raise RuntimeError(
            f"otbox CLI resolution: could not probe bare python3 ({py!r}): "
            f"{exc}. Set OT_CLI_BIN to a wrapper that pins PYTHONPATH to the "
            "worktree src + a python with the opentraces deps installed."
        ) from exc
    if probe.returncode != 0:
        raise RuntimeError(
            "otbox CLI resolution: the bare 'python3' fallback cannot "
            "'import opentraces.cli' (no repo .venv, no OT_CLI_BIN). "
            "Every checkpoint build would fail with a ModuleNotFoundError "
            "three subprocess layers deep. Fix: export OT_CLI_BIN to a "
            "wrapper that pins PYTHONPATH=<worktree>/src:"
            "<worktree>/packages/opentraces-schema/src and exec's a python "
            "with the CLI deps (click, pydantic, ...). Probe stderr:\n"
            f"{(probe.stderr or probe.stdout).strip()[:400]}"
        )


def isolated_env(
    box: Box, extra: dict | None = None, *, live_hf: bool = False
) -> dict[str, str]:
    """Build the environment for any command run inside a box.

    Redirects ``HOME`` so ``~/.opentraces`` and ``~/.claude`` resolve into
    the box, disables implicit HF tokens, strips any inherited HF
    credentials, and points the dataset fake-remote seam at the box's
    fake-remote root. This is the single chokepoint that guarantees zero
    host residue.

    When ``live_hf=True`` (the opt-in ``live_hf`` capability lane), the HF
    seams flip: the fake-remote env vars are *not* set and a real HF token
    is injected so the CLI talks to ``huggingface.co``. ``HOME`` and all
    git hermeticity stay unchanged, so ``~/.opentraces`` state remains
    box-isolated — only the remote backend goes live.
    """
    env = os.environ.copy()
    env["HOME"] = str(box.home)
    if live_hf:
        # Live lane: do NOT strip credentials or set the fake-remote seams.
        # Inject the real token (config._resolve_hf_token checks HF_TOKEN first).
        token = (
            os.environ.get("OPENTRACES_LIVE_HF_TOKEN")
            or os.environ.get("HF_TOKEN")
        )
        if token:
            env["HF_TOKEN"] = token
        env.pop("HF_HUB_DISABLE_IMPLICIT_TOKEN", None)
        env.pop("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", None)
        env.pop("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", None)
    else:
        env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
        env.pop("HF_TOKEN", None)
        env.pop("HUGGINGFACE_TOKEN", None)
        env.pop("HUGGING_FACE_HUB_TOKEN", None)
        env["OPENTRACES_PLAN058_FAKE_REMOTE_ROOT"] = str(box.fake_remote)
    # Keep the box hermetic: never let a real git identity or pager leak in.
    env["GIT_CONFIG_GLOBAL"] = str(box.home / ".gitconfig")
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CEILING_DIRECTORIES"] = str(box.root)
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["OTBOX_BOX_ID"] = box.box_id
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env
