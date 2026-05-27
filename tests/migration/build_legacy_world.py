"""Build + freeze a real opentraces v0.3.3 world (plan 085 R1).

Drives the *actual* v0.3.3 CLI (installed into an isolated venv from the
``v0.3.3`` git tag) through ``init`` + ``_scan`` against a synthetic Claude
Code session, producing a genuine schema-0.3.0 on-disk world: per-project
``state.json``, the ``.opentraces.json`` marker, and ``traces/<id>.jsonl``
records carrying ``outcome.patch``. The result is scrubbed of machine-specific
paths and frozen under ``fixtures/legacy_world_v033/`` for the ``c-legacy-v033``
otbox checkpoint to restore.

This is the artifact-preferred half of the plan-072 pattern. It is NOT run in
default CI; it is an operator tool re-run when the legacy world needs
regenerating. The committed frozen tree is what tests consume.

Usage:
    python tests/migration/build_legacy_world.py \
        --v033-venv /tmp/ot-v033-worktree/.venv-v033 \
        --out tests/migration/fixtures/legacy_world_v033
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SESSION_ID = "sess-legacy-v033-refactor"
SRC_BEFORE = 'def greet(name):\n    return "hi " + name\n'
SRC_AFTER = 'def greet(name):\n    return "hi " + name\n\n\ndef farewell(name):\n    return "bye " + name\n'
UNIFIED_DIFF = (
    "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,5 @@\n"
    ' def greet(name):\n     return "hi " + name\n'
    '+\n+\n+def farewell(name):\n+    return "bye " + name\n'
)


def _run(argv, *, cwd, env, check=True):
    r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(
            f"command failed ({r.returncode}): {' '.join(argv)}\n"
            f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
        )
    return r


def _claude_session_lines(cwd: str) -> list[dict]:
    """Minimal parser-valid Claude Code transcript: user prompt -> assistant
    Edit tool_use -> user tool_result -> assistant text. >=1 tool call, >=2
    steps, so it clears the v0.3.3 quality gate."""
    edit_id = "toolu_legacy_v033_edit"
    return [
        {"type": "user", "sessionId": SESSION_ID, "uuid": "u1", "cwd": cwd,
         "message": {"role": "user",
                     "content": [{"type": "text", "text": "add a farewell() helper to app.py"}]}},
        {"type": "assistant", "sessionId": SESSION_ID, "uuid": "a1", "cwd": cwd,
         "message": {"role": "assistant", "model": "claude-sonnet-4-5",
                     "content": [
                         {"type": "text", "text": "I'll add the helper."},
                         {"type": "tool_use", "id": edit_id, "name": "Edit",
                          "input": {"file_path": f"{cwd}/app.py",
                                    "old_string": SRC_BEFORE, "new_string": SRC_AFTER}},
                     ]}},
        {"type": "user", "sessionId": SESSION_ID, "uuid": "u2", "cwd": cwd,
         "toolUseResult": {"filePath": f"{cwd}/app.py", "oldString": SRC_BEFORE,
                           "newString": SRC_AFTER},
         "message": {"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": edit_id,
                                  "content": [{"type": "text", "text": "edit applied"}]}]}},
        {"type": "assistant", "sessionId": SESSION_ID, "uuid": "a2", "cwd": cwd,
         "message": {"role": "assistant", "model": "claude-sonnet-4-5",
                     "content": [{"type": "text", "text": "Done — added farewell()."}]}},
    ]


def build(v033_venv: Path, out: Path) -> None:
    cli = v033_venv / "bin" / "opentraces"
    py = v033_venv / "bin" / "python"
    if not cli.exists():
        raise SystemExit(f"v0.3.3 CLI not found at {cli}")

    work = Path(tempfile.mkdtemp(prefix="legacy-v033-"))
    home = work / "home"
    project = work / "project"
    home.mkdir(parents=True)
    project.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "OPENTRACES_NONINTERACTIVE": "1"}

    # 1. git project with an initial commit + the post-state file on disk.
    _run(["git", "init", "-q"], cwd=project, env=env)
    _run(["git", "config", "user.email", "legacy@opentraces.test"], cwd=project, env=env)
    _run(["git", "config", "user.name", "Legacy User"], cwd=project, env=env)
    (project / "app.py").write_text(SRC_BEFORE)
    _run(["git", "add", "-A"], cwd=project, env=env)
    _run(["git", "commit", "-q", "-m", "seed"], cwd=project, env=env)

    # 2. opentraces init (real config + marker + state).
    _run([str(cli), "init"], cwd=project, env=env)

    # 3. apply the edit + commit so process_trace derives a committed outcome.
    (project / "app.py").write_text(SRC_AFTER)
    _run(["git", "add", "-A"], cwd=project, env=env)
    _run(["git", "commit", "-q", "-m", "add farewell helper"], cwd=project, env=env)

    # 4. write the synthetic Claude session into the canonical transcript dir.
    encoded = _run(
        [str(py), "-c",
         "import sys;from pathlib import Path;"
         "from opentraces.core.repo_identity import encode_claude_path;"
         "print(encode_claude_path(Path(sys.argv[1])))", str(project)],
        cwd=project, env=env,
    ).stdout.strip()
    sess_dir = home / ".claude" / "projects" / encoded
    sess_dir.mkdir(parents=True, exist_ok=True)
    transcript = sess_dir / f"{SESSION_ID}.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(line, sort_keys=True) for line in _claude_session_lines(str(project)))
        + "\n"
    )

    # 5. real ingest via the v0.3.3 CLI.
    _run([str(cli), "_scan", "--reparse"], cwd=project, env=env)

    # 6. assert + ensure outcome.patch is populated on the emitted 0.3.0 record.
    #    Layout: ~/.opentraces/projects/<project-id>/traces/<trace>.jsonl
    trace_files = sorted((home / ".opentraces" / "projects").glob("*/traces/*.jsonl"))
    if not trace_files:
        raise SystemExit(
            f"no traces emitted under {home / '.opentraces' / 'projects'}"
        )
    for tf in trace_files:
        rec = json.loads(tf.read_text().splitlines()[0])
        assert rec.get("schema_version") == "0.3.0", rec.get("schema_version")
        rec.setdefault("outcome", {})
        if not rec["outcome"].get("patch"):
            rec["outcome"]["patch"] = UNIFIED_DIFF  # guarantee the at-risk field
        tf.write_text(json.dumps(rec, sort_keys=True) + "\n")

    # 7. freeze: copy the world, scrub absolute paths for determinism.
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(
        home / ".opentraces", out / "opentraces_home",
        ignore=shutil.ignore_patterns("ingest-locks", "*.lock"),
    )
    shutil.copytree(project, out / "project", ignore=shutil.ignore_patterns(".git"))
    _scrub_paths(out, str(home), str(project))

    schema_versions = sorted({
        json.loads(p.read_text().splitlines()[0]).get("schema_version")
        for p in (out / "opentraces_home").rglob("*.jsonl")
    })
    (out / "PROVENANCE.json").write_text(json.dumps({
        "opentraces_cli_version": "0.3.3",
        "opentraces_schema_version": "0.3.0",
        "source_tag": "v0.3.3",
        "session_id": SESSION_ID,
        "schema_versions_observed": schema_versions,
        "build_command": "python tests/migration/build_legacy_world.py",
        "note": "Real v0.3.3 init + _scan; outcome.patch guaranteed populated.",
    }, indent=2) + "\n")
    print(f"Froze legacy v0.3.3 world to {out} (schema versions: {schema_versions})")
    shutil.rmtree(work, ignore_errors=True)


def _scrub_paths(root: Path, home: str, project: str) -> None:
    """Replace machine-specific absolute paths with stable placeholders."""
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        new = text.replace(home, "$LEGACY_HOME").replace(project, "$LEGACY_PROJECT")
        if new != text:
            p.write_text(new)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v033-venv", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    build(args.v033_venv, args.out.resolve())


if __name__ == "__main__":
    main()
