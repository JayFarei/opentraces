"""Real-box custody control for corrupt Claude preferences in A6.

This control seeds a product-user ``~/.claude.json`` with invalid JSON before
materializing ``agent-ready``.  The recipe must refuse at the no-write
preferences-validation seam, before the Claude installer can rewrite or back
up the pre-existing bytes.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from opentraces.core.arena.box import CrabboxRefusal, CrabboxRuntime


REPOSITORY = Path(__file__).resolve().parents[2]


def verify() -> None:
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="opentraces-a6-corrupt-preferences-") as directory:
        root = Path(directory)
        evidence = root / "evidence"
        runtime = CrabboxRuntime(home=Path.home(), ssh_config=root / "missing-ssh-config")
        runtime.configure_run_evidence(evidence)
        box = None
        os.chdir(REPOSITORY)
        try:
            box = runtime.lease()
            seeded = runtime.exec(
                box,
                [
                    "sh",
                    "-c",
                    "set -eu; "
                    "git init --initial-branch=main >/dev/null; "
                    "git config user.name 'A6 corrupt preferences custody'; "
                    "git config user.email 'a6-corrupt-preferences@example.invalid'; "
                    "printf '%s\\n' seed > seed.txt; "
                    "git add seed.txt; "
                    "git commit -m 'Seed corrupt preferences custody workspace' >/dev/null",
                ],
                timeout=30,
                timing_path=evidence / "remote-seed-timing.json",
            )
            if seeded.returncode != 0:
                raise AssertionError("could not seed the corrupt-preferences workspace")

            corrupted = runtime.exec(
                box,
                [
                    "sh",
                    "-c",
                    "set -eu; "
                    "if ! id -u opentraces-product >/dev/null 2>&1; then "
                    "sudo useradd --create-home --home-dir /home/opentraces-product "
                    "--shell /bin/sh opentraces-product; "
                    "fi; "
                    "sudo -H -u opentraces-product -- python3 -c "
                    '"from pathlib import Path; '
                    "Path.home().joinpath('.claude.json').write_bytes(b'not-json')\"",
                ],
                timeout=30,
                timing_path=evidence / "write-corrupt-preferences-timing.json",
            )
            if corrupted.returncode != 0:
                raise AssertionError("could not seed corrupt product-user preferences")

            try:
                runtime.materialize(box, "agent-ready", repository=REPOSITORY)
            except CrabboxRefusal as exc:
                refusal_code = exc.code
            else:
                raise AssertionError("corrupt preferences did not refuse agent-ready")

            observed = runtime.exec(
                box,
                [
                    "sudo",
                    "-H",
                    "-u",
                    "opentraces-product",
                    "--",
                    "python3",
                    "-c",
                    "import json; from pathlib import Path; "
                    "path=Path.home()/'.claude.json'; "
                    "print(json.dumps({'original_exists':path.exists(), "
                    "'original_unchanged':path.exists() and "
                    "path.read_bytes()==b'not-json'}))",
                ],
                timeout=30,
                timing_path=evidence / "observe-corrupt-preferences-timing.json",
            )
            if observed.returncode != 0:
                raise AssertionError("could not observe corrupt preferences after refusal")
            state = json.loads(observed.stdout)
            if refusal_code != "agent_harness_preferences_invalid":
                raise AssertionError(
                    "agent-ready returned the wrong refusal for corrupt preferences: "
                    f"{refusal_code!r}"
                )
            if not state.get("original_exists") or not state.get("original_unchanged"):
                raise AssertionError(
                    "agent-ready mutated pre-existing corrupt preferences: "
                    f"state={state!r}"
                )
            print(
                "A6 corrupt preferences custody control: PASS "
                f"refusal={refusal_code} state={state!r}"
            )
        finally:
            try:
                if box is not None:
                    runtime.release(box)
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    verify()
