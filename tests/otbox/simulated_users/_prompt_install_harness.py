#!/usr/bin/env python3
"""Deterministic PTY harness for the prompt install/auth footage scenario."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_PROMPT_STRINGS = [
    "opentraces --json auth whoami",
    "opentraces auth login --device-timeout 180",
    "outside this session",
    "Do not ask me to paste an HF token into agent chat.",
    "command -v claude",
    "command -v codex",
    "command -v pi",
    "opentraces setup claude-code",
    "opentraces setup codex-cli",
    "opentraces setup pi --dry-run --json",
    "opentraces setup skill",
    "opentraces setup git",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run(argv: list[str], *, expect_rc: int = 0) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(argv)}", flush=True)
    proc = subprocess.run(argv, capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        print(out[:1600], flush=True)
    if err:
        print(err[:1600], flush=True)
    print(f"rc={proc.returncode}", flush=True)
    if proc.returncode != expect_rc:
        raise RuntimeError(
            f"expected rc {expect_rc} for {' '.join(argv)}, got {proc.returncode}"
        )
    return proc


def _assert_path(path: Path) -> None:
    print(f"assert path exists: {path}", flush=True)
    if not path.exists():
        raise RuntimeError(f"missing expected path: {path}")


def _opentraces_json(stdout: str) -> dict:
    marker = "---OPENTRACES_JSON---"
    if marker not in stdout:
        raise RuntimeError("opentraces output did not contain JSON sentinel")
    payload = stdout.split(marker, 1)[1].strip()
    return json.loads(payload)


def _verify_prompt_contract() -> None:
    prompt = _repo_root() / "web" / "site" / "src" / "content" / "agent-setup-prompt.md"
    text = prompt.read_text(encoding="utf-8")
    print(f"checking homepage prompt: {prompt}", flush=True)
    for needle in REQUIRED_PROMPT_STRINGS:
        if needle not in text:
            raise RuntimeError(f"homepage prompt missing {needle!r}")
        print(f"prompt contains: {needle}", flush=True)


def _verify_install_flow() -> None:
    project = Path.cwd()
    home = Path(os.environ["HOME"])
    cli = project / ".testvenv" / "bin" / "opentraces"
    if not cli.exists():
        raise RuntimeError(f"missing opentraces CLI at {cli}")

    _run(["git", "init", "--quiet", "--initial-branch=main"])
    _run(["git", "config", "user.email", "otbox-prompt-install@opentraces.local"])
    _run(["git", "config", "user.name", "otbox prompt install"])
    _run([str(cli), "--version"])
    _run([str(cli), "config", "tracking-mode", "global"])
    _run([str(cli), "setup", "claude-code"])
    _run([str(cli), "setup", "codex-cli"])
    _run([str(cli), "setup", "pi", "--local", "--json"])
    _run([str(cli), "setup", "skill"])
    _run([str(cli), "setup", "git"])

    whoami = _run([str(cli), "--json", "auth", "whoami"])
    whoami_payload = _opentraces_json(whoami.stdout)
    if whoami_payload.get("authenticated") is not False:
        raise RuntimeError(f"expected unauthenticated whoami, got {whoami_payload}")
    print("auth whoami returned authenticated=false", flush=True)

    bucket = _run([str(cli), "setup", "bucket", "--json"], expect_rc=3)
    bucket_text = f"{bucket.stdout}\n{bucket.stderr}"
    if "opentraces auth login" not in bucket_text:
        raise RuntimeError("bucket setup did not tell the user to run auth login")
    print("bucket setup produced outside-auth follow-up", flush=True)

    doctor = _run([str(cli), "--json", "doctor"])
    doctor_payload = _opentraces_json(doctor.stdout)
    installers = {
        item.get("installer")
        for item in doctor_payload.get("doctor", {}).get("hooks", [])
        if isinstance(item, dict)
    }
    expected_installers = {"claude-code", "codex-cli", "pi", "git", "skill"}
    if not expected_installers.issubset(installers):
        raise RuntimeError(f"doctor missing hook installers: {expected_installers - installers}")
    print("doctor reported Claude, Codex, Pi, git, and skill installers", flush=True)

    _assert_path(home / ".claude" / "settings.json")
    _assert_path(home / ".codex" / "hooks.json")
    _assert_path(home / ".pi" / "agent" / "settings.json")
    _assert_path(home / ".agents" / "skills" / "opentraces" / "SKILL.md")
    _assert_path(project / ".git" / "hooks" / "opentraces-post-commit")


def main() -> int:
    if "--version" in sys.argv:
        print("otbox-prompt-install-harness 1.0.0")
        return 0

    print("OpenTraces prompt install/auth verifier harness ready.", flush=True)
    for line in sys.stdin:
        command = line.strip().lower()
        if command in {"exit", "quit"}:
            print("Goodbye.", flush=True)
            return 0
        try:
            _verify_prompt_contract()
            _verify_install_flow()
        except Exception as exc:  # noqa: BLE001 - surfaced in footage
            print(f"PROMPT-INSTALL-AUTH-FLOW-FAIL: {exc}", flush=True)
            return 1
        print("PROMPT-INSTALL-AUTH-FLOW-PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
