#!/usr/bin/env python3
"""tmux-driven test harness for trace_attribution_spike_v2.

Loads SCENARIOS from declarative TOML files and executes them against real
`claude` REPL sessions in tmux. Each scenario is a sequence of steps mixing
agent-driven prompts and out-of-Claude project actions (file edits, shell
commands, commits), followed by assertions over the resulting attribution.

Usage:
  attribution_v2_harness.py                              # all scenarios
  attribution_v2_harness.py baseline                     # by name (under scripts/scenarios/)
  attribution_v2_harness.py path/to/scenario.toml        # by file path
  attribution_v2_harness.py -v small_tweak               # verbose
  attribution_v2_harness.py --keep small_tweak           # preserve dirs/sessions

Scenario schema (TOML):

  name = "small_tweak"
  description = "..."
  project_dir = "/tmp/ot-h-small"   # optional override

  # Each step is one operation. Mix in any order.
  [[steps]]
  type = "reset"                    # rm -rf + git init + initial commit
  initial_readme = "# sandbox\n"    # optional, default: "# test sandbox\n"

  [[steps]]
  type = "claude"                   # run a claude session
  label = "a"                       # referenced in assertions
  prompts = ["Write hello.txt", "done"]
  quiesce = 5                       # optional: secs of JSONL silence = done

  [[steps]]
  type = "commit"
  message = "scenario"
  files = ["hello.txt"]             # optional: stage these only (default: all)

  [[steps]]
  type = "write_file"               # simulate human/external edit
  path = "config.yaml"
  content = "key: value\n"

  [[steps]]
  type = "delete_file"
  path = "obsolete.md"

  [[steps]]
  type = "shell"                    # arbitrary shell in project_dir
  command = "mkdir -p data && touch data/marker"

  [[steps]]
  type = "git"
  args = ["checkout", "-b", "feature"]

  [[steps]]
  type = "wait"
  seconds = 2

  [[steps]]
  type = "start_watcher"            # spawns the watcher daemon

  [[steps]]
  type = "stop_watcher"

  # Assertions run after all steps. Trace labels resolve to runtime trace_ids.
  # Special trace = "pre-audit" matches any pre-audit:* (init commit, etc.)

  [[assertions]]
  file = "config/server.py"
  trace = "a"
  lines = 18
  tolerance = 1                     # optional; default 0

  [[assertions]]
  file = "README.md"
  trace = "pre-audit"
  lines_min = 1                     # alternative to exact `lines`

  [[assertions]]
  file = "image.png"
  status = "missing_from_audit"     # assert the file isn't blame-able
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


SPIKE = Path(__file__).parent / "trace_attribution_spike_v2.py"
SCENARIOS_DIR = Path(__file__).parent / "scenarios"
TMUX = "tmux"
# `c` is the user's shell alias for `claude --dangerously-skip-permissions`.
# Resolved by the interactive shell tmux spawns, so aliases work.
CLAUDE = "c"

PROMPT_TIMEOUT = 240
STARTUP_TIMEOUT = 30
QUIESCE_SECONDS = 5
POLL_INTERVAL = 0.4
INIT_COMMIT_DATE = "2026-04-13T09:00:00Z"

PERMISSIVE_SETTINGS = {
    "permissions": {"defaultMode": "acceptEdits"},
}


# --------------------------------------------------------------------------- #
# Tiny utilities
# --------------------------------------------------------------------------- #

def _enc(p: Path) -> str:
    """Claude Code encodes project paths into directory names by replacing
    every non-alphanumeric, non-hyphen character with `-`. So `/private/tmp/
    ot-h-small_tweak` becomes `-private-tmp-ot-h-small-tweak` (underscore
    AND slash both become dashes; hyphens preserved).
    """
    s = str(p.resolve())
    return "".join(c if c.isalnum() or c == "-" else "-" for c in s)


def _run(cmd, cwd=None, env=None, check=True, verbose=False):
    if verbose:
        print(f"  $ {' '.join(str(c) for c in cmd)}", file=sys.stderr)
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(
            f"cmd failed: {cmd}\n  stderr: {r.stderr}\n  stdout: {r.stdout}"
        )
    return r


def _proj_dir_for(project_cwd: Path) -> Path:
    return Path.home() / ".claude" / "projects" / _enc(project_cwd)


# --------------------------------------------------------------------------- #
# Claude REPL context (tmux + JSONL polling)
# --------------------------------------------------------------------------- #

@dataclass
class ClaudeContext:
    name: str
    project_dir: Path
    verbose: bool = False
    jsonl_path: Path | None = None
    trace_id: str | None = None
    _existing_jsonls: set = field(default_factory=set)

    def start(self) -> None:
        """Spawn claude in a fresh tmux session, dismiss any trust prompt,
        and wait for the welcome screen. The JSONL is NOT created here;
        claude only writes a session JSONL once the user submits a prompt.
        Discovery of the JSONL happens in the first prompt() call.
        """
        proj = _proj_dir_for(self.project_dir)
        self._existing_jsonls = (
            {p.name for p in proj.glob("*.jsonl")} if proj.is_dir() else set()
        )
        _run([TMUX, "kill-session", "-t", self.name], check=False)
        _run([TMUX, "new-session", "-d", "-s", self.name,
              "-c", str(self.project_dir), "-x", "200", "-y", "50"],
             verbose=self.verbose)
        _run([TMUX, "send-keys", "-t", self.name, CLAUDE, "Enter"],
             verbose=self.verbose)
        deadline = time.time() + STARTUP_TIMEOUT
        trust_handled = False
        welcome_markers = ("Claude Code v", "Welcome back", "/init")
        while time.time() < deadline:
            pane = subprocess.run(
                [TMUX, "capture-pane", "-t", self.name, "-p"],
                capture_output=True, text=True,
            ).stdout
            # Welcome screen visible? Then claude is ready for input.
            if any(m in pane for m in welcome_markers):
                if self.verbose:
                    print(f"  [{self.name}] welcome screen ready",
                          file=sys.stderr)
                time.sleep(1.0)
                return
            # Trust prompt? Dismiss it.
            if not trust_handled and (
                "trust this folder" in pane.lower()
                or "trust this directory" in pane.lower()
            ):
                if self.verbose:
                    print(f"  [{self.name}] trust prompt → sending Enter",
                          file=sys.stderr)
                _run([TMUX, "send-keys", "-t", self.name, "Enter"])
                trust_handled = True
                time.sleep(0.8)
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"[{self.name}] claude welcome never appeared "
                           f"in {STARTUP_TIMEOUT}s")

    def prompt(self, text: str, timeout: float = PROMPT_TIMEOUT,
               quiesce: float = QUIESCE_SECONDS) -> None:
        if self.verbose:
            short = text if len(text) <= 80 else text[:77] + "..."
            print(f"  [{self.name}] → {short}", file=sys.stderr)
        _run([TMUX, "send-keys", "-t", self.name, "-l", text])
        _run([TMUX, "send-keys", "-t", self.name, "Enter"])
        # Slash commands (/clear, /compact, etc.) are local UI actions that
        # don't produce assistant turns, so JSONL-growth detection never
        # triggers. Use a short fixed pause. For /clear specifically, the
        # current session may be retired and a new JSONL opened on the next
        # real prompt — reset jsonl_path so discovery re-runs.
        if text.lstrip().startswith("/"):
            time.sleep(3.0)
            if text.lstrip().startswith("/clear"):
                if self.jsonl_path:
                    self._existing_jsonls.add(self.jsonl_path.name)
                self.jsonl_path = None
            return
        # Discover the JSONL the first time. Claude writes it on first prompt
        # submission, so this only has to happen once per session.
        if not self.jsonl_path:
            self._discover_jsonl()
        last_size = self._size()
        last_change = time.time()
        deadline = time.time() + timeout
        saw_growth = False
        while time.time() < deadline:
            size = self._size()
            if size != last_size:
                last_size = size
                last_change = time.time()
                saw_growth = True
            elif saw_growth and (time.time() - last_change >= quiesce):
                if self.verbose:
                    print(f"  [{self.name}] ← quiesced ({last_size}B)",
                          file=sys.stderr)
                return
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"[{self.name}] prompt didn't complete in {timeout}s")

    def _discover_jsonl(self, timeout: float = 30) -> None:
        proj = _proj_dir_for(self.project_dir)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proj.is_dir():
                for p in proj.glob("*.jsonl"):
                    if p.name not in self._existing_jsonls:
                        self.jsonl_path = p
                        self.trace_id = p.stem
                        if self.verbose:
                            print(f"  [{self.name}] jsonl = {p.name}",
                                  file=sys.stderr)
                        return
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"[{self.name}] jsonl never appeared after first prompt")

    def _size(self) -> int:
        try:
            return self.jsonl_path.stat().st_size
        except FileNotFoundError:
            return 0

    def exit(self) -> None:
        try:
            _run([TMUX, "send-keys", "-t", self.name, "/exit", "Enter"],
                 check=False)
            time.sleep(1.5)
        finally:
            _run([TMUX, "kill-session", "-t", self.name], check=False)


# --------------------------------------------------------------------------- #
# Scenario model + loader
# --------------------------------------------------------------------------- #

@dataclass
class Scenario:
    name: str
    description: str
    project_dir: Path
    steps: list[dict]
    assertions: list[dict]
    source_file: Path | None = None


def load_scenario(path: Path) -> Scenario:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    name = data.get("name") or path.stem
    return Scenario(
        name=name,
        description=data.get("description", ""),
        project_dir=Path(data.get("project_dir") or f"/tmp/ot-h-{name}"),
        steps=list(data.get("steps") or []),
        assertions=list(data.get("assertions") or []),
        source_file=path,
    )


def discover_scenarios(selectors: list[str]) -> list[Scenario]:
    """Resolve CLI args into Scenario objects.
    Each selector is either a name (looked up in scripts/scenarios/<name>.toml)
    or a path to a .toml file. With no selectors, returns all .toml under
    scripts/scenarios/ alphabetically.
    """
    out: list[Scenario] = []
    if not selectors:
        if not SCENARIOS_DIR.is_dir():
            return []
        for p in sorted(SCENARIOS_DIR.glob("*.toml")):
            out.append(load_scenario(p))
        return out
    for sel in selectors:
        p = Path(sel)
        if p.suffix == ".toml" and p.is_file():
            out.append(load_scenario(p))
        else:
            named = SCENARIOS_DIR / f"{sel}.toml"
            if not named.is_file():
                raise FileNotFoundError(
                    f"scenario not found: {sel} "
                    f"(tried {p} and {named})"
                )
            out.append(load_scenario(named))
    return out


# --------------------------------------------------------------------------- #
# Step + assertion executor
# --------------------------------------------------------------------------- #

@dataclass
class RunState:
    """Mutable state during a scenario run."""
    project_dir: Path
    verbose: bool = False
    # Labels can map to MORE than one trace_id. /clear retires the current
    # session and opens a new one under the same logical label; the harness
    # records both session_ids under the label so assertions sum contributions.
    label_to_trace: dict[str, set[str]] = field(default_factory=dict)
    contexts: list[ClaudeContext] = field(default_factory=list)
    watcher_proc: subprocess.Popen | None = None
    # Plan-043 phase 1 additions — populated by run_backfill / invoke_cli.
    backfill_reports: list = field(default_factory=list)
    last_cli: dict = field(default_factory=dict)


# --- step implementations ---

def _step_reset(state: RunState, params: dict) -> None:
    p = state.project_dir
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True)
    _run(["git", "init", "-q", "-b", "main"], cwd=p)
    sd = p / ".claude"
    sd.mkdir()
    (sd / "settings.local.json").write_text(json.dumps(PERMISSIVE_SETTINGS))
    initial_readme = params.get("initial_readme", "# test sandbox\n")
    (p / "README.md").write_text(initial_readme)
    env = {**os.environ,
           "GIT_AUTHOR_DATE": INIT_COMMIT_DATE,
           "GIT_COMMITTER_DATE": INIT_COMMIT_DATE}
    _run(["git", "add", "."], cwd=p)
    _run(["git", "-c", "user.email=h@h", "-c", "user.name=H",
          "commit", "-q", "-m", "init"], cwd=p, env=env)
    # scrub stale jsonls for this path so prior runs don't leak
    pdir = _proj_dir_for(p)
    if pdir.is_dir():
        for j in pdir.glob("*.jsonl"):
            j.unlink()


def _step_claude(state: RunState, params: dict) -> None:
    label = params.get("label") or f"c{len(state.contexts)+1}"
    # Existing label + reusing it: only allowed if the step's intent is to
    # extend the same logical trace. Be strict — reject by default so
    # typos don't silently merge two scenarios' assertions together.
    if label in state.label_to_trace:
        raise ValueError(f"duplicate claude label: {label}")
    prompts = params.get("prompts") or []
    if not prompts:
        raise ValueError(f"claude step '{label}' has no prompts")
    quiesce = float(params.get("quiesce", QUIESCE_SECONDS))
    timeout = float(params.get("timeout", PROMPT_TIMEOUT))
    # cleanup="exit" sends /exit (clean shutdown).
    # cleanup="kill" kills the tmux session abruptly (simulates terminal close).
    cleanup = params.get("cleanup", "exit")
    ctx = ClaudeContext(name=f"otah-{state.project_dir.name[:20]}-{label}",
                        project_dir=state.project_dir,
                        verbose=state.verbose)
    state.contexts.append(ctx)
    ctx.start()
    try:
        for prompt in prompts:
            ctx.prompt(prompt, timeout=timeout, quiesce=quiesce)
            if ctx.trace_id:
                state.label_to_trace.setdefault(label, set()).add(ctx.trace_id)
    finally:
        if ctx.trace_id:
            state.label_to_trace.setdefault(label, set()).add(ctx.trace_id)
        if cleanup == "kill":
            _run([TMUX, "kill-session", "-t", ctx.name], check=False)
        else:
            ctx.exit()


def _step_commit(state: RunState, params: dict) -> None:
    msg = params.get("message") or "harness commit"
    files = params.get("files")
    allow_empty = bool(params.get("allow_empty", False))
    if files:
        _run(["git", "add", *files], cwd=state.project_dir)
    else:
        _run(["git", "add", "-A"], cwd=state.project_dir)
    cmd = ["git", "-c", "user.email=h@h", "-c", "user.name=H",
           "commit", "-m", msg]
    if allow_empty:
        cmd.append("--allow-empty")
    _run(cmd, cwd=state.project_dir)


def _step_write_file(state: RunState, params: dict) -> None:
    path = state.project_dir / params["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(params.get("content", ""))


def _step_delete_file(state: RunState, params: dict) -> None:
    path = state.project_dir / params["path"]
    if path.exists():
        path.unlink()


def _step_shell(state: RunState, params: dict) -> None:
    cmd = params["command"]
    r = subprocess.run(cmd, shell=True, cwd=state.project_dir,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"shell failed: {cmd}\n  {r.stderr}")
    if state.verbose and r.stdout.strip():
        print(f"  [shell] {r.stdout.strip()[:200]}", file=sys.stderr)


def _step_git(state: RunState, params: dict) -> None:
    args = params["args"]
    _run(["git", *args], cwd=state.project_dir, verbose=state.verbose)


def _step_wait(state: RunState, params: dict) -> None:
    time.sleep(float(params.get("seconds", 1)))


def _step_start_watcher(state: RunState, params: dict) -> None:
    if state.watcher_proc:
        return
    state.watcher_proc = subprocess.Popen(
        [sys.executable, str(SPIKE), "watch", str(state.project_dir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)  # let initial poll happen


def _step_stop_watcher(state: RunState, params: dict) -> None:
    if not state.watcher_proc:
        return
    state.watcher_proc.terminate()
    try:
        state.watcher_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        state.watcher_proc.kill()
    state.watcher_proc = None
    time.sleep(0.5)


def _step_init_opentraces(state: RunState, params: dict) -> None:
    """Drop an .opentraces.json marker so get_project_dir works.

    Kept side-effect-free on the global registry — we only need the marker.
    """
    import uuid as _uuid
    marker = state.project_dir / ".opentraces.json"
    if not marker.is_file():
        marker.write_text(json.dumps(
            {"project_id": _uuid.uuid4().hex, "policy": {}}
        ))
    # Stage + commit so HEAD sees the marker.
    if params.get("commit", True):
        _run(["git", "add", ".opentraces.json"], cwd=state.project_dir,
             check=False)
        _run(["git", "-c", "user.email=h@h", "-c", "user.name=H",
              "commit", "-q", "-m", "add opentraces marker"],
             cwd=state.project_dir, check=False)


def _step_run_backfill(state: RunState, params: dict) -> None:
    """Invoke core.backfill in-process. Stashes the report on state."""
    from opentraces.core import backfill as _backfill
    mode = params.get("mode", "incremental")
    max_commits = int(params.get("max_commits", 100))
    if mode == "dry_run":
        report = _backfill.run_dry_run(state.project_dir, max_commits=max_commits)
    elif mode == "rebuild" or mode == "full":
        report = _backfill.run_full(state.project_dir, max_commits=max_commits)
    else:
        report = _backfill.run_incremental(state.project_dir,
                                           max_commits=max_commits)
    state.backfill_reports.append(report)


def _step_invoke_cli(state: RunState, params: dict) -> None:
    """Run ``./otd <args...>`` via subprocess, capture stdout/stderr/exit."""
    args = list(params.get("args") or [])
    repo_root = Path(__file__).resolve().parent.parent
    otd = repo_root / "otd"
    r = subprocess.run([str(otd), *args], cwd=state.project_dir,
                       capture_output=True, text=True)
    state.last_cli = {
        "args": args,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "returncode": r.returncode,
    }


def _step_delete_cache_entry(state: RunState, params: dict) -> None:
    """Delete a specific attribution cache file by commit ref."""
    from opentraces.core.cache import AttributionCache
    ref = params["ref"]
    sha = subprocess.check_output(
        ["git", "rev-parse", ref], cwd=state.project_dir, text=True
    ).strip()
    c = AttributionCache(state.project_dir)
    p = c.attribution_path(sha)
    if p.is_file():
        p.unlink()


STEP_HANDLERS = {
    "reset": _step_reset,
    "claude": _step_claude,
    "commit": _step_commit,
    "write_file": _step_write_file,
    "delete_file": _step_delete_file,
    "shell": _step_shell,
    "git": _step_git,
    "wait": _step_wait,
    "start_watcher": _step_start_watcher,
    "stop_watcher": _step_stop_watcher,
    "init_opentraces": _step_init_opentraces,
    "run_backfill": _step_run_backfill,
    "invoke_cli": _step_invoke_cli,
    "delete_cache_entry": _step_delete_cache_entry,
}


def execute_step(state: RunState, step: dict) -> None:
    t = step.get("type")
    if t not in STEP_HANDLERS:
        raise ValueError(f"unknown step type: {t!r}")
    STEP_HANDLERS[t](state, step)


# --- assertion implementation ---

def _resolve_trace(state: RunState, label: str) -> set[str] | None:
    if label == "pre-audit":
        return None  # signal special handling
    return state.label_to_trace.get(label)


def _attribution_for(state: RunState, ref: str = "HEAD") -> dict:
    _run([sys.executable, str(SPIKE), "build", str(state.project_dir)])
    r = _run([sys.executable, str(SPIKE), "attribute",
              str(state.project_dir), ref, "--json"])
    return json.loads(r.stdout)


def _assert_backfill_scenario(state: RunState, a: dict) -> str:
    """Assertions specific to plan-043 phase 1 backfill scenarios.

    Supported kinds:
      - cache_file_exists: ref="HEAD" exists=true/false
      - cli_stdout_matches / cli_stderr_matches: last invoke_cli must contain substr
      - cli_returncode: exact exit code match
      - report_field: name=... equals=... on the last backfill report
    """
    kind = a.get("kind")
    if kind == "cache_file_exists":
        from opentraces.core.cache import AttributionCache
        ref = a.get("ref", "HEAD")
        expected = bool(a.get("exists", True))
        sha = subprocess.check_output(
            ["git", "rev-parse", ref], cwd=state.project_dir, text=True
        ).strip()
        c = AttributionCache(state.project_dir)
        actual = c.has_attribution(sha)
        if actual != expected:
            raise AssertionError(
                f"cache_file_exists(ref={ref}, sha={sha[:8]}): "
                f"expected {expected}, got {actual}"
            )
        return f"cache[{ref}]={actual} ✓"
    if kind == "cli_stdout_matches":
        needle = a["contains"]
        haystack = state.last_cli.get("stdout", "")
        if needle not in haystack:
            raise AssertionError(
                f"cli_stdout_matches: {needle!r} not in stdout "
                f"(stdout={haystack[:200]!r})"
            )
        return f"stdout contains {needle!r} ✓"
    if kind == "cli_returncode":
        expected = int(a["equals"])
        actual = int(state.last_cli.get("returncode", -1))
        if expected != actual:
            raise AssertionError(
                f"cli_returncode: expected {expected}, got {actual} "
                f"(stderr={state.last_cli.get('stderr','')[:200]!r})"
            )
        return f"returncode={actual} ✓"
    if kind == "report_field":
        if not state.backfill_reports:
            raise AssertionError("report_field: no backfill reports recorded")
        report = state.backfill_reports[-1]
        name = a["name"]
        actual = getattr(report, name)
        if "equals" in a:
            expected = a["equals"]
            if actual != expected:
                raise AssertionError(
                    f"report.{name}: expected {expected!r}, got {actual!r}"
                )
            return f"report.{name}={actual!r} ✓"
        if "min" in a:
            if actual < a["min"]:
                raise AssertionError(
                    f"report.{name}: expected ≥{a['min']}, got {actual}"
                )
            return f"report.{name}={actual} (≥{a['min']}) ✓"
        if "max" in a:
            if actual > a["max"]:
                raise AssertionError(
                    f"report.{name}: expected ≤{a['max']}, got {actual}"
                )
            return f"report.{name}={actual} (≤{a['max']}) ✓"
        raise ValueError(f"report_field needs 'equals', 'min', or 'max'")
    return ""  # fall-through sentinel, caller handles legacy assertion


def run_assertions(state: RunState, assertions: list[dict]) -> list[str]:
    """Returns a list of human-readable assertion-pass messages.
    Raises AssertionError on the first failure with full context."""
    if not assertions:
        return ["(no assertions specified)"]
    # Plan-043 phase-1 kinds don't need an audit build — handle separately so
    # backfill scenarios don't reinvoke the spike unnecessarily.
    phase1_kinds = {"cache_file_exists", "cli_stdout_matches",
                    "cli_stderr_matches", "cli_returncode", "report_field"}
    if all(a.get("kind") in phase1_kinds for a in assertions):
        return [_assert_backfill_scenario(state, a) for a in assertions]
    result = _attribution_for(state)
    msgs: list[str] = []
    for i, a in enumerate(assertions):
        if a.get("kind") in phase1_kinds:
            msgs.append(_assert_backfill_scenario(state, a))
            continue
        path = a.get("file")
        if not path:
            raise ValueError(f"assertion {i} has no 'file' key")
        files = result.get("files", {})
        if path not in files:
            raise AssertionError(f"file '{path}' missing from attribution")
        f = files[path]
        # status check
        if "status" in a:
            if f.get("status") != a["status"]:
                raise AssertionError(
                    f"file '{path}': status={f.get('status')!r}, "
                    f"expected {a['status']!r}"
                )
            msgs.append(f"{path}: status={a['status']} ✓")
            continue
        if f.get("status") != "attributed":
            raise AssertionError(
                f"file '{path}': not attributed (status={f.get('status')!r})"
            )
        # `total_lines` is self-contained: field name carries both target and value
        if "total_lines" in a:
            expected = int(a["total_lines"])
            actual = f.get("total_lines", 0)
            if actual != expected:
                raise AssertionError(
                    f"{path} TOTAL: expected {expected}, got {actual}"
                )
            msgs.append(f"{path}/TOTAL: {actual} ✓")
            continue
        # determine expected line count for the assertion
        if "trace" in a:
            label = a["trace"]
            tids = _resolve_trace(state, label)
            if label == "pre-audit":
                actual = sum(e["count"] for k, e in f["by_trace"].items()
                             if k.startswith("pre-audit:"))
            else:
                if not tids:
                    raise ValueError(
                        f"assertion {i}: trace label '{label}' not "
                        f"resolved (known: {list(state.label_to_trace)})"
                    )
                actual = sum(f["by_trace"].get(t, {}).get("count", 0)
                             for t in tids)
            target_str = label
        else:
            raise ValueError(
                f"assertion {i} for {path}: must include 'trace', "
                f"'total_lines', or 'status'"
            )
        # range / equality checks
        if "lines_min" in a:
            mn = int(a["lines_min"])
            if actual < mn:
                raise AssertionError(
                    f"{path} [{target_str}]: expected ≥{mn} lines, got {actual}"
                )
            msgs.append(f"{path}/{target_str}: {actual} (≥{mn}) ✓")
        elif "lines_max" in a:
            mx = int(a["lines_max"])
            if actual > mx:
                raise AssertionError(
                    f"{path} [{target_str}]: expected ≤{mx} lines, got {actual}"
                )
            msgs.append(f"{path}/{target_str}: {actual} (≤{mx}) ✓")
        elif "lines" in a:
            expected = int(a["lines"])
            tol = int(a.get("tolerance", 0))
            if abs(actual - expected) > tol:
                breakdown = {k[:8]: v["count"]
                             for k, v in f["by_trace"].items()}
                raise AssertionError(
                    f"{path} [{target_str}]: expected {expected}±{tol}, "
                    f"got {actual} (breakdown: {breakdown})"
                )
            msgs.append(f"{path}/{target_str}: {actual} ✓")
        else:
            raise ValueError(
                f"assertion {i} for {path}: needs 'lines', 'lines_min', "
                f"'lines_max', 'total_lines', or 'status'"
            )
    return msgs


# --------------------------------------------------------------------------- #
# Scenario runner
# --------------------------------------------------------------------------- #

def run_scenario(s: Scenario, verbose: bool = False) -> tuple[bool, str]:
    state = RunState(project_dir=s.project_dir, verbose=verbose)
    try:
        for i, step in enumerate(s.steps):
            t = step.get("type", "?")
            if verbose:
                print(f"  step {i+1}/{len(s.steps)}: {t}", file=sys.stderr)
            try:
                execute_step(state, step)
            except Exception as e:
                return False, f"step {i+1} ({t}) failed: {e}"
        msgs = run_assertions(state, s.assertions)
        return True, "; ".join(msgs)
    except AssertionError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        if state.watcher_proc:
            try: _step_stop_watcher(state, {})
            except Exception: pass
        for ctx in state.contexts:
            try: _run([TMUX, "kill-session", "-t", ctx.name], check=False)
            except Exception: pass


def teardown_scenario(s: Scenario) -> None:
    if s.project_dir.exists():
        shutil.rmtree(s.project_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__.split("\n\n", 1)[0],
        epilog="See module docstring for full TOML scenario schema.",
    )
    ap.add_argument("scenarios", nargs="*",
                    help="scenario name(s) or path(s) to .toml file(s); "
                         "default: all under scripts/scenarios/")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--keep", action="store_true",
                    help="don't clean up project dir / tmux sessions")
    ap.add_argument("--list", action="store_true",
                    help="list available scenarios and exit")
    args = ap.parse_args()

    if args.list:
        scens = discover_scenarios([])
        if not scens:
            print(f"no scenarios in {SCENARIOS_DIR}")
            return 0
        for s in scens:
            print(f"  {s.name:<30} {s.description.splitlines()[0] if s.description else ''}")
        return 0

    try:
        scens = discover_scenarios(args.scenarios)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not scens:
        print(f"no scenarios found (looked in {SCENARIOS_DIR})", file=sys.stderr)
        return 2

    GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
    passed = 0
    failures: list[tuple[str, str]] = []

    for s in scens:
        t0 = time.time()
        print(f"→ {s.name}{DIM}  ({s.source_file})  → {s.project_dir}{RESET}",
              flush=True)
        ok, detail = run_scenario(s, verbose=args.verbose)
        elapsed = time.time() - t0
        marker = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        print(f"  {marker} {detail}  {DIM}[{elapsed:.1f}s]{RESET}")
        if ok:
            passed += 1
            if not args.keep:
                teardown_scenario(s)
        else:
            failures.append((s.name, detail))

    print()
    total = len(scens)
    if passed == total:
        print(f"{GREEN}{total}/{total} passed{RESET}")
    else:
        print(f"{RED}{passed}/{total} passed, {len(failures)} failed{RESET}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
