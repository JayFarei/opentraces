#!/usr/bin/env python3
"""PROBE A10 — no full-dir traces scan on a present-trace path (epic #169).

Committed, grader-owned, location-relative version of the loopcraft forge draft
`a10_no_glob_guard.py`.

Done-claim: no read-verb call site scans the full ~1,482-file traces dir on a
PRESENT-trace path (the unbounded `glob("*.jsonl")` fallthrough is gone).

Method (T3, grounded re-observation of real runtime behavior): monkeypatch the
directory-enumeration PRIMITIVES and count every FULL-DIR scan of the real
project traces dir, recording the offending call site (file:lineno) from the
live stack. Then drive each of the FOUR named call sites with a PRESENT trace
and assert ZERO full-dir scans:

  SITE 1  cli.trail_helpers._load_trace_step_summaries   (by trace_id)
  SITE 2  core.trails.query._load_step_metadata          (by trace_id)
  SITE 3  core.trace_meta.resolve_trace_meta             (by SESSION_ID, cold)
  SITE 4  core.trace_meta.resolve_trace_id_prefix        (by trace_id)

Counted as a full-dir scan (an OFFENDER):
  - pathlib.Path.glob(self, "*.jsonl")             on a known traces dir, AND
  - os.scandir / os.listdir / pathlib.Path.iterdir on a known traces dir,
    when NOT issued from inside pathlib/glob machinery (i.e. a real scan-PRIMITIVE
    swap by production code). This closes the evasion where a "fix" merely swaps
    glob for scandir/listdir/iterdir against the same dir.
NOT counted (the cheap, bounded fast path):
  - prefix globs `<id>*.jsonl` (pattern endswith "*.jsonl" but != "*.jsonl"), and
  - the scandir those prefix globs issue internally (caller frame is glob.py /
    pathlib), so a legitimate prefix fast-path never reads as an offender.

Defeater killed: a glob fallthrough silently fires on a present trace addressed
by its session_id (session_id != trace_id for ALL real files; filenames are the
trace_id), where the prefix fast-path misses and the code falls through to the
full scan. SITE 3 is exercised BY SESSION_ID precisely to flush that defeater.
The fix resolves session_id via the existing id index/manifest in the store (the
present-trace precondition guarantees a non-empty, correct result), never a scan.

False-RED / tamper guards:
- Present-trace precondition: every probed function must return correct,
  non-empty data (resolved trace_id / non-empty step maps). A fix cannot pass by
  making lookups return empty -- that trips the precondition and aborts
  (PRECONDITION-FAILED), it does NOT count as GREEN.
- lru_cache cold-measure: trace_meta caches are cleared before SITE 3 so we
  observe the real code path, not a warm no-op (a warm cache would hide the
  full scan and fake a GREEN).
- Per-site attribution: offenders are reported as file:lineno, so the assertion
  cannot be weakened by fixing one site while another regresses; GREEN requires
  ALL four call sites silent.
- Scan-primitive-swap guard: glob, scandir, listdir, AND iterdir are all
  intercepted, so swapping the enumeration primitive cannot evade the guard.

Location-relative: imports the code-under-test from the `src/` that lives next
to this file (a grader worktree exercises THAT worktree's `src/`). The witness
trace lives in the real per-project store (`~/.opentraces/projects/<slug>/`),
which carries the real session->trace index/manifest the fix consults; the
store is located slug-agnostically (never a hardcoded slug) and the test
transparently restores the real store globals even under an isolating conftest
that redirects them to a tmp HOME. The drive is strictly READ-ONLY (it never
enlists or writes), so the real store is untouched.

Exit codes (standalone): 0 GREEN (no full-dir scan on any present-trace path);
1 RED (>=1 full-dir scan observed); 3 PRECONDITION/SETUP-INVALID (store/data
absent or a site returned empty/incorrect data). Any non-GREEN FAILS pytest.
"""
from __future__ import annotations

import glob as _glob_mod
import json
import os
import pathlib
import subprocess
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

# --- id/oracle pins (kept EXACTLY as the forge draft) -------------------------
# Constructed via the EXACT filename (filename == trace_id), never via a glob,
# so building the fixture cannot itself trip the counter.
PRESENT_TRACE_ID = "00416c26-20dc-4811-9f37-d1096f2460e5"
PRESENT_SESSION_ID = "019da4ee-9843-72e2-a8e0-0fcd4365c330"
_WITNESS_FILE = f"{PRESENT_TRACE_ID}.jsonl"

# stdlib enumeration machinery: scandir issued from inside these is attributable
# to a glob / iterdir we already classify, NOT a production primitive swap.
_GLOB_PY = _glob_mod.__file__
_PATHLIB_DIR = os.path.dirname(pathlib.__file__)

# Every store global the isolating conftest may redirect; we transparently point
# them back at the REAL store for the read-only drive (and restore after).
_STORE_GLOBALS = (
    "OPENTRACES_DIR",
    "PROJECTS_DIR",
    "CONFIG_PATH",
    "CREDENTIALS_PATH",
    "STAGING_DIR",
)


def _candidate_projects_dirs(config_mod) -> list[Path]:
    """Ordered candidates for the REAL `~/.opentraces/projects` dir.

    Covers both the standalone case (the live module global is already real) and
    an isolating conftest (the module global is a tmp dir; the real home is
    recovered from the password DB, $HOME-independent, then from expanduser).
    """
    out: list[Path] = []

    def _add(p) -> None:
        if p is None:
            return
        try:
            pp = Path(p)
        except Exception:
            return
        if pp not in out:
            out.append(pp)

    _add(getattr(config_mod, "PROJECTS_DIR", None))
    try:
        import pwd  # unix-only; robust against a monkeypatched $HOME

        _add(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".opentraces" / "projects")
    except Exception:
        pass
    _add(Path(os.path.expanduser("~")) / ".opentraces" / "projects")
    return out


def _find_real_projects_dir(config_mod) -> Path | None:
    """Return the projects dir that actually owns the witness trace (slug-agnostic)."""
    for cand in _candidate_projects_dirs(config_mod):
        try:
            if cand.is_dir() and any(cand.glob(f"*/traces/{_WITNESS_FILE}")):
                return cand.resolve()
        except Exception:
            continue
    return None


def _resolve_opted_in_project(config_mod) -> Path | None:
    """Return the opted-in working-tree dir whose marker maps to the witness store.

    Slug-agnostic: try the common-git-dir parent (the real opted-in working
    tree, works on the main checkout and from a linked grader worktree), then
    this REPO, then cwd, then every registered project. Never hardcodes a slug.
    """
    get_project_traces_dir = config_mod.get_project_traces_dir

    def _owns(cand: Path) -> bool:
        try:
            return (get_project_traces_dir(cand) / _WITNESS_FILE).exists()
        except Exception:
            return False

    candidates: list[Path] = []
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if common.returncode == 0 and common.stdout.strip():
            candidates.append(Path(common.stdout.strip()).parent)
    except Exception:
        pass
    candidates.append(REPO)
    try:
        candidates.append(Path.cwd())
    except Exception:
        pass
    try:
        for p in config_mod.opted_in_projects(config_mod.load_config()):
            candidates.append(Path(p))
    except Exception:
        pass

    for cand in candidates:
        if _owns(cand):
            return cand
    return None


def _run() -> int:
    from opentraces.cli import trail_helpers  # noqa: E402
    from opentraces.core import config as config_mod  # noqa: E402
    from opentraces.core import paths as paths_mod  # noqa: E402
    from opentraces.core import trace_meta  # noqa: E402
    from opentraces.core.trails import query as trails_query  # noqa: E402

    # --- point the store globals back at the REAL store (restore in finally) --
    real_projects = _find_real_projects_dir(config_mod)
    if real_projects is None:
        print(
            f"PRECONDITION-FAILED: no opted-in store owns witness {_WITNESS_FILE}",
            file=sys.stderr,
        )
        return 3
    real_ot = real_projects.parent
    real_values = {
        "OPENTRACES_DIR": real_ot,
        "PROJECTS_DIR": real_projects,
        "CONFIG_PATH": real_ot / "config.json",
        "CREDENTIALS_PATH": real_ot / "credentials",
        "STAGING_DIR": real_ot / "staging",
    }
    saved: list[tuple[object, str, object]] = []
    for mod in (config_mod, paths_mod):
        for attr in _STORE_GLOBALS:
            if hasattr(mod, attr):
                saved.append((mod, attr, getattr(mod, attr)))
                setattr(mod, attr, real_values[attr])

    try:
        return _drive(config_mod, trail_helpers, trails_query, trace_meta)
    finally:
        for mod, attr, old in saved:
            setattr(mod, attr, old)


def _drive(config_mod, trail_helpers, trails_query, trace_meta) -> int:
    get_project_dir = config_mod.get_project_dir
    get_project_traces_dir = config_mod.get_project_traces_dir

    # --- resolve the opted-in working tree + its real traces dir(s) -----------
    project_dir = _resolve_opted_in_project(config_mod)
    if project_dir is None:
        print(
            f"PRECONDITION-FAILED: no opted-in project resolves to witness "
            f"{_WITNESS_FILE}",
            file=sys.stderr,
        )
        return 3
    try:
        traces_dir = get_project_traces_dir(project_dir).resolve()
        tm_traces_dir = (get_project_dir(project_dir) / "traces").resolve()
    except Exception as exc:
        print(f"PRECONDITION-FAILED: traces dir unresolved: {exc!r}", file=sys.stderr)
        return 3
    known_traces_dirs = {str(traces_dir), str(tm_traces_dir)}

    # --- present-trace fixture validity (never built via a glob) --------------
    fixture_path = traces_dir / _WITNESS_FILE
    if not fixture_path.exists():
        print(f"PRECONDITION-FAILED: fixture {fixture_path} absent", file=sys.stderr)
        return 3
    try:
        _rec = json.loads(fixture_path.read_text().splitlines()[0])
    except Exception as exc:
        print(f"PRECONDITION-FAILED: fixture unreadable: {exc!r}", file=sys.stderr)
        return 3
    if _rec.get("trace_id") != PRESENT_TRACE_ID or _rec.get("session_id") != PRESENT_SESSION_ID:
        print("PRECONDITION-FAILED: fixture ids drifted", file=sys.stderr)
        return 3
    if not (_rec.get("steps") or []):
        print("PRECONDITION-FAILED: fixture has no steps", file=sys.stderr)
        return 3

    # --- instrumentation ------------------------------------------------------
    # offenders: list of (site_label, primitive, detail, caller_site)
    offenders: list[tuple[str, str, str, str]] = []
    benign_prefix: list[str] = []

    _real_glob = pathlib.Path.glob
    _real_iterdir = pathlib.Path.iterdir
    _real_scandir = os.scandir
    _real_listdir = os.listdir

    def _resolved_str(p):
        """Best-effort absolute string for a path-like; None for fds/garbage."""
        try:
            raw = os.fspath(p)
        except TypeError:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            return str(Path(raw).resolve())
        except Exception:
            return raw.rstrip("/")

    def _caller_site() -> str:
        # stack[-1]=this helper, stack[-2]=primitive wrapper, stack[-3]=caller.
        stack = traceback.extract_stack()
        fr = stack[-3] if len(stack) >= 3 else stack[-1]
        return f"{Path(fr.filename).name}:{fr.lineno}"

    def _caller_is_internal() -> bool:
        """True when the primitive was issued from inside glob/pathlib machinery.

        A glob (or iterdir) issues scandir internally; that scandir is already
        classified by the glob/iterdir wrapper, so it must not be re-counted. A
        real production primitive swap has a production-source caller frame.
        """
        stack = traceback.extract_stack()
        if len(stack) < 3:
            return False
        fn = stack[-3].filename
        return fn == _GLOB_PY or fn.startswith(_PATHLIB_DIR)

    def _instrumented_glob(self, pattern, *a, **k):
        try:
            resolved = str(self.resolve())
        except Exception:
            resolved = str(self)
        if resolved in known_traces_dirs:
            if pattern == "*.jsonl":
                offenders.append(("?", "glob", pattern, _caller_site()))
            elif isinstance(pattern, str) and pattern.endswith("*.jsonl"):
                benign_prefix.append(pattern)
        return _real_glob(self, pattern, *a, **k)

    def _instrumented_iterdir(self, *a, **k):
        try:
            resolved = str(self.resolve())
        except Exception:
            resolved = str(self)
        if resolved in known_traces_dirs and not _caller_is_internal():
            offenders.append(("?", "iterdir", resolved, _caller_site()))
        return _real_iterdir(self, *a, **k)

    def _instrumented_scandir(path=".", *a, **k):
        resolved = _resolved_str(path)
        if resolved in known_traces_dirs and not _caller_is_internal():
            offenders.append(("?", "scandir", resolved, _caller_site()))
        return _real_scandir(path, *a, **k)

    def _instrumented_listdir(path=".", *a, **k):
        resolved = _resolved_str(path)
        if resolved in known_traces_dirs and not _caller_is_internal():
            offenders.append(("?", "listdir", resolved, _caller_site()))
        return _real_listdir(path, *a, **k)

    def _tag(start: int, label: str) -> list[str]:
        """Tag offenders accrued since `start` with the site label; return sites."""
        sites = []
        for i in range(start, len(offenders)):
            _lbl, prim, detail, site = offenders[i]
            offenders[i] = (label, prim, detail, site)
            sites.append(f"{prim}@{site}")
        return sites

    results: dict[str, list[str]] = {}
    precondition_failures: list[str] = []

    pathlib.Path.glob = _instrumented_glob
    pathlib.Path.iterdir = _instrumented_iterdir
    os.scandir = _instrumented_scandir
    os.listdir = _instrumented_listdir
    try:
        # ---- SITE 1: trail_helpers._load_trace_step_summaries (present trace_id)
        start = len(offenders)
        summaries = trail_helpers._load_trace_step_summaries(project_dir, PRESENT_TRACE_ID)
        results["site1_load_trace_step_summaries"] = _tag(start, "site1")
        if not summaries:
            precondition_failures.append(
                "site1: _load_trace_step_summaries returned empty (trace not really present)"
            )

        # ---- SITE 2: query._load_step_metadata (present trace_id) -------------
        start = len(offenders)
        meta = trails_query._load_step_metadata(project_dir, [PRESENT_TRACE_ID])
        results["site2_load_step_metadata"] = _tag(start, "site2")
        if not meta:
            precondition_failures.append(
                "site2: _load_step_metadata returned empty (trace not really present)"
            )

        # ---- SITE 3: trace_meta.resolve_trace_meta via _resolve_cached --------
        #      Driven BY SESSION_ID (the defeater: prefix fast-path misses).
        #      Cold-measured: clear the lru_cache so we observe the real path.
        trace_meta.clear_cache()
        trace_meta._resolve_cached.cache_clear()
        start = len(offenders)
        tm = trace_meta.resolve_trace_meta(project_dir, PRESENT_SESSION_ID)
        results["site3_resolve_cached_by_session_id"] = _tag(start, "site3")
        if tm is None or getattr(tm, "trace_id", None) != PRESENT_TRACE_ID:
            precondition_failures.append(
                f"site3: resolve_trace_meta(session_id) did not resolve present trace (got {tm!r})"
            )

        # ---- SITE 4: trace_meta.resolve_trace_id_prefix (present trace_id) ----
        start = len(offenders)
        resolved_tid = trace_meta.resolve_trace_id_prefix(project_dir, PRESENT_TRACE_ID)
        results["site4_resolve_trace_id_prefix"] = _tag(start, "site4")
        if resolved_tid != PRESENT_TRACE_ID:
            precondition_failures.append(
                f"site4: resolve_trace_id_prefix did not resolve present trace (got {resolved_tid!r})"
            )
    finally:
        pathlib.Path.glob = _real_glob
        pathlib.Path.iterdir = _real_iterdir
        os.scandir = _real_scandir
        os.listdir = _real_listdir

    # --- verdict --------------------------------------------------------------
    print("project_dir:", project_dir)
    print("known_traces_dirs:", sorted(known_traces_dirs))
    print("present trace_id:", PRESENT_TRACE_ID, "session_id:", PRESENT_SESSION_ID)
    print("benign prefix-glob patterns seen:", sorted(set(benign_prefix)))
    print("--- per-site full-dir scan offenders (primitive@file:lineno) ---")
    for site, hits in results.items():
        print(f"  {site}: {hits if hits else 'none'}")

    if precondition_failures:
        print("PRECONDITION-FAILED:", file=sys.stderr)
        for f in precondition_failures:
            print("  " + f, file=sys.stderr)
        return 3

    total = sum(len(h) for h in results.values())
    if total > 0:
        print(f"\nRED: {total} full-dir traces scan(s) on a present-trace path; offenders above.")
        return 1
    print("\nGREEN: no full-dir traces scan on any present-trace path.")
    return 0


def test_probe_a10_no_glob_guard():
    rc = _run()
    assert rc != 3, (
        "A10 PRECONDITION/SETUP-INVALID: witness store/data not present or a "
        "probed site returned empty/incorrect data (see captured output)"
    )
    assert rc == 0, (
        "A10 RED: full-dir traces scan(s) on a present-trace path "
        "(unbounded glob/scandir/listdir/iterdir over the traces dir)"
    )


if __name__ == "__main__":
    sys.exit(_run())
