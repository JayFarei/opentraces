#!/usr/bin/env python3
"""Self-test for trace_attribution_spike_v2.

Constructs synthetic trace data under ~/.claude/projects/-tmp-ot-v2-selftest/
and ~/.claude/file-history/<synthetic-trace-id>/, builds audit history, runs
attribution, asserts per-line ownership is correct. Cleans up after.

Run:  python3 scripts/attribution_v2_selftest.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


# Fixed past timestamp for the init commit so all synthetic-trace timestamps
# (also fixed, see scenarios below) are always AFTER it, regardless of when
# the test is run. This is what makes `git rev-list --before=<snap_ts> HEAD`
# actually find the init commit as the audit history's baseline parent.
INIT_COMMIT_DATE = "2026-04-13T09:00:00Z"


# Synthetic IDs, clearly-labeled for easy cleanup
TRACE_A = "selftest-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TRACE_B = "selftest-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
TRACE_C = "selftest-cccc-cccc-cccc-cccccccccccc"

PROJECT = Path("/tmp/ot-v2-selftest")
CLAUDE_HOME = Path.home() / ".claude"
SPIKE = Path(__file__).parent / "trace_attribution_spike_v2.py"


# --------------------------------------------------------------------------- #
# Scaffolding
# --------------------------------------------------------------------------- #

def proj_dir_encoded() -> Path:
    enc = str(PROJECT.resolve()).replace("/", "-")
    return CLAUDE_HOME / "projects" / enc


def cleanup() -> None:
    if PROJECT.exists():
        shutil.rmtree(PROJECT, ignore_errors=True)
    pd = proj_dir_encoded()
    if pd.exists():
        shutil.rmtree(pd, ignore_errors=True)
    for tid in (TRACE_A, TRACE_B, TRACE_C):
        d = CLAUDE_HOME / "file-history" / tid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def run(cmd, cwd=None, check=True, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed: {cmd}\nstderr: {r.stderr}\nstdout: {r.stdout}")
    return r


def setup_project() -> None:
    PROJECT.mkdir(parents=True)
    run(["git", "init", "-q", "-b", "main"], cwd=PROJECT)
    (PROJECT / "README.md").write_text("# test sandbox\n")
    run(["git", "add", "."], cwd=PROJECT)
    dated_env = {**os.environ,
                 "GIT_AUTHOR_DATE": INIT_COMMIT_DATE,
                 "GIT_COMMITTER_DATE": INIT_COMMIT_DATE}
    run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", "init"], cwd=PROJECT, env=dated_env)
    proj_dir_encoded().mkdir(parents=True)


def blob_name(path: str, version: int) -> str:
    h = hashlib.sha256(path.encode()).hexdigest()[:16]
    return f"{h}@v{version}"


def write_blob(trace_id: str, file_path: str, version: int, content: str) -> None:
    d = CLAUDE_HOME / "file-history" / trace_id
    d.mkdir(parents=True, exist_ok=True)
    (d / blob_name(file_path, version)).write_text(content)


def make_snapshot(message_id: str, timestamp: str,
                  backups: dict[str, int]) -> dict:
    """backups: {file_path: version}"""
    tfb = {p: {"backupFileName": blob_name(p, v),
               "version": v, "backupTime": timestamp}
           for p, v in backups.items()}
    return {
        "type": "file-history-snapshot",
        "messageId": message_id,
        "snapshot": {"messageId": message_id, "trackedFileBackups": tfb},
    }


def write_jsonl(trace_id: str, entries: list) -> None:
    p = proj_dir_encoded() / f"{trace_id}.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def spike_build() -> None:
    run([sys.executable, str(SPIKE), "build", str(PROJECT)])


def spike_attribute() -> dict:
    r = run([sys.executable, str(SPIKE), "attribute", str(PROJECT), "--json"])
    return json.loads(r.stdout)


# --------------------------------------------------------------------------- #
# Assertions
# --------------------------------------------------------------------------- #

def expect_file_attribution(result: dict, path: str,
                            expected_counts: dict[str, int]) -> None:
    """Assert file `path` has `expected_counts = {trace_id: line_count}`."""
    if path not in result["files"]:
        raise AssertionError(f"{path}: not in attribution result")
    data = result["files"][path]
    if data["status"] != "attributed":
        raise AssertionError(f"{path}: status={data['status']}, expected attributed")
    actual = {tid: e["count"] for tid, e in data["by_trace"].items()}
    for tid, cnt in expected_counts.items():
        a = actual.get(tid, 0)
        if a != cnt:
            raise AssertionError(
                f"{path}: trace {tid[:16]} expected {cnt} lines, got {a} "
                f"(full breakdown: {actual})"
            )


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #

def scenario_baseline() -> None:
    """One trace creates one file, one commit — all lines → that trace."""
    cleanup(); setup_project()

    write_blob(TRACE_A, "hello.txt", 2, "hello from trace A\n")
    write_jsonl(TRACE_A, [
        make_snapshot("msg-a1", "2026-04-13T10:00:00.000Z",
                      {"hello.txt": 2}),
    ])
    (PROJECT / "hello.txt").write_text("hello from trace A\n")
    run(["git", "add", "."], cwd=PROJECT)
    run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", "add hello"], cwd=PROJECT)

    spike_build()
    result = spike_attribute()
    expect_file_attribution(result, "hello.txt", {TRACE_A: 1})


def scenario_two_sessions_different_files() -> None:
    """Trace A writes README; Trace B writes config.yaml. Commit. Expect split."""
    cleanup(); setup_project()

    readme_content = "# Project\n\nThe quick brown fox.\nJumps over the lazy dog.\n"
    config_content = "version: 1\ndebug: false\nport: 8080\n"

    write_blob(TRACE_A, "README.md", 2, readme_content)
    write_jsonl(TRACE_A, [
        make_snapshot("msg-a1", "2026-04-13T10:00:00.000Z",
                      {"README.md": 2}),
    ])

    write_blob(TRACE_B, "config.yaml", 2, config_content)
    write_jsonl(TRACE_B, [
        make_snapshot("msg-b1", "2026-04-13T11:00:00.000Z",
                      {"config.yaml": 2}),
    ])

    (PROJECT / "README.md").write_text(readme_content)
    (PROJECT / "config.yaml").write_text(config_content)
    run(["git", "add", "."], cwd=PROJECT)
    run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", "scaffold"], cwd=PROJECT)

    spike_build()
    result = spike_attribute()
    expect_file_attribution(result, "README.md", {TRACE_A: 4})
    expect_file_attribution(result, "config.yaml", {TRACE_B: 3})


def scenario_small_tweak() -> None:
    """Trace A writes 20 lines. Trace B changes lines 5 and 15.
    Expect 18 → A, 2 → B. This is the case patch-id cannot decompose.
    """
    cleanup(); setup_project()

    lines = [f"line {i}\n" for i in range(1, 21)]
    content_a = "".join(lines)
    lines_b = list(lines)
    lines_b[4] = "line 5 (modified by B)\n"
    lines_b[14] = "line 15 (modified by B)\n"
    content_b = "".join(lines_b)

    write_blob(TRACE_A, "feature.py", 2, content_a)
    write_jsonl(TRACE_A, [
        make_snapshot("msg-a1", "2026-04-13T10:00:00.000Z",
                      {"feature.py": 2}),
    ])

    # Trace B: first snapshot sees v2 == content_a (pre-edit state unchanged),
    # then v3 == content_b
    write_blob(TRACE_B, "feature.py", 2, content_a)
    write_blob(TRACE_B, "feature.py", 3, content_b)
    write_jsonl(TRACE_B, [
        make_snapshot("msg-b1", "2026-04-13T11:00:00.000Z",
                      {"feature.py": 2}),
        make_snapshot("msg-b2", "2026-04-13T11:05:00.000Z",
                      {"feature.py": 3}),
    ])

    (PROJECT / "feature.py").write_text(content_b)
    run(["git", "add", "."], cwd=PROJECT)
    run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", "add feature"], cwd=PROJECT)

    spike_build()
    result = spike_attribute()
    expect_file_attribution(result, "feature.py", {TRACE_A: 18, TRACE_B: 2})


def scenario_three_way_layered() -> None:
    """Trace A writes 5 lines. Trace B adds 3 lines in the middle.
    Trace C modifies 1 of A's surviving lines. Commit.
    Expect: A=4 (5 - 1 modified by C), B=3, C=1.
    """
    cleanup(); setup_project()

    a_content = "alpha\nbravo\ncharlie\ndelta\necho\n"
    # B inserts 3 lines between charlie and delta
    b_content = "alpha\nbravo\ncharlie\nnew1\nnew2\nnew3\ndelta\necho\n"
    # C modifies 'bravo' to 'BRAVO'
    c_content = "alpha\nBRAVO\ncharlie\nnew1\nnew2\nnew3\ndelta\necho\n"

    # Trace A
    write_blob(TRACE_A, "notes.md", 2, a_content)
    write_jsonl(TRACE_A, [
        make_snapshot("msg-a1", "2026-04-13T10:00:00.000Z",
                      {"notes.md": 2}),
    ])

    # Trace B (v2 = A's output, v3 = B's output)
    write_blob(TRACE_B, "notes.md", 2, a_content)
    write_blob(TRACE_B, "notes.md", 3, b_content)
    write_jsonl(TRACE_B, [
        make_snapshot("msg-b1", "2026-04-13T11:00:00.000Z", {"notes.md": 2}),
        make_snapshot("msg-b2", "2026-04-13T11:05:00.000Z", {"notes.md": 3}),
    ])

    # Trace C (v3 = B's output, v4 = C's output)
    write_blob(TRACE_C, "notes.md", 3, b_content)
    write_blob(TRACE_C, "notes.md", 4, c_content)
    write_jsonl(TRACE_C, [
        make_snapshot("msg-c1", "2026-04-13T12:00:00.000Z", {"notes.md": 3}),
        make_snapshot("msg-c2", "2026-04-13T12:05:00.000Z", {"notes.md": 4}),
    ])

    (PROJECT / "notes.md").write_text(c_content)
    run(["git", "add", "."], cwd=PROJECT)
    run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", "notes"], cwd=PROJECT)

    spike_build()
    result = spike_attribute()
    expect_file_attribution(result, "notes.md", {TRACE_A: 4, TRACE_B: 3, TRACE_C: 1})


def scenario_working_tree_captured() -> None:
    """Simulate Bash-created files captured by the watcher (working-tree
    source). One file via file-history (tracked Edit/Write), one via working-
    tree (sidecar). Both attributed to their respective traces.
    """
    cleanup(); setup_project()

    # Trace A: file-history path
    write_blob(TRACE_A, "tracked.md", 2, "tracked content\n")
    write_jsonl(TRACE_A, [
        make_snapshot("msg-a1", "2026-04-13T10:00:00.000Z", {"tracked.md": 2}),
    ])

    # Trace B: simulate watcher capturing a Bash-created file
    (PROJECT / "bash_created.md").write_text("created by bash\n")
    sha = subprocess.run(
        ["git", "-C", str(PROJECT), "hash-object", "-w", "bash_created.md"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    sidecar = PROJECT / ".git" / "opentraces-watcher-events.jsonl"
    with sidecar.open("a") as fp:
        fp.write(json.dumps({
            "trace_id": TRACE_B,
            "timestamp": "2026-04-13T11:00:00.000Z",
            "files": {"bash_created.md": sha},
            "source": "working-tree",
            "message_id": "wt-test",
        }) + "\n")

    # Stage final state and commit
    (PROJECT / "tracked.md").write_text("tracked content\n")
    run(["git", "add", "."], cwd=PROJECT)
    run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", "tracked + bash"], cwd=PROJECT)

    spike_build()
    result = spike_attribute()
    expect_file_attribution(result, "tracked.md", {TRACE_A: 1})
    expect_file_attribution(result, "bash_created.md", {TRACE_B: 1})


def scenario_pre_session_content() -> None:
    """README.md exists before any trace (from init commit). Trace A appends to it.
    Commit. Expect A=new lines, pre-audit=original lines.
    """
    cleanup(); setup_project()

    original = "# test sandbox\n"           # this is what init commit wrote
    appended = original + "Added by A.\nMore context.\n"

    # Trace A captures v1 = original (pre-state) and v2 = appended
    write_blob(TRACE_A, "README.md", 1, original)
    write_blob(TRACE_A, "README.md", 2, appended)
    write_jsonl(TRACE_A, [
        make_snapshot("msg-a1", "2026-04-13T10:00:00.000Z", {"README.md": 1}),
        make_snapshot("msg-a2", "2026-04-13T10:05:00.000Z", {"README.md": 2}),
    ])

    (PROJECT / "README.md").write_text(appended)
    run(["git", "add", "."], cwd=PROJECT)
    run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", "expand readme"], cwd=PROJECT)

    spike_build()
    result = spike_attribute()
    data = result["files"]["README.md"]
    by_trace = data["by_trace"]

    a_count = by_trace.get(TRACE_A, {}).get("count", 0)
    pre_count = sum(e["count"] for tid, e in by_trace.items()
                     if tid.startswith("pre-audit:"))
    if a_count != 2:
        raise AssertionError(f"expected A=2 (Added + More), got {a_count}; "
                             f"breakdown={ {k: v['count'] for k,v in by_trace.items()} }")
    if pre_count != 1:
        raise AssertionError(f"expected pre-audit=1 (# test sandbox), got {pre_count}; "
                             f"breakdown={ {k: v['count'] for k,v in by_trace.items()} }")


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

SCENARIOS = [
    ("baseline", scenario_baseline),
    ("two_sessions_different_files", scenario_two_sessions_different_files),
    ("small_tweak (the patch-id killer)", scenario_small_tweak),
    ("three_way_layered", scenario_three_way_layered),
    ("pre_session_content", scenario_pre_session_content),
    ("working_tree_captured (the bash gap)", scenario_working_tree_captured),
]


def main() -> int:
    GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
    passed = 0
    failures: list[tuple[str, str]] = []
    for name, fn in SCENARIOS:
        try:
            fn()
            print(f"  {GREEN}✓{RESET} {name}")
            passed += 1
        except AssertionError as e:
            print(f"  {RED}✗{RESET} {name}")
            print(f"    {DIM}{e}{RESET}")
            failures.append((name, str(e)))
        except Exception as e:
            print(f"  {RED}!{RESET} {name}: unexpected {type(e).__name__}")
            print(f"    {DIM}{e}{RESET}")
            failures.append((name, f"{type(e).__name__}: {e}"))

    cleanup()
    total = len(SCENARIOS)
    print()
    if passed == total:
        print(f"{GREEN}all {total} scenario(s) passed{RESET}")
        return 0
    else:
        print(f"{RED}{passed}/{total} passed, {len(failures)} failed{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
