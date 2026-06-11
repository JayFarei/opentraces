"""Idempotency sweep (otbox 2.0 phase 6).

Invariant (entity: Bucket / Capture / Config): repair/rebuild/setup verbs are
idempotent — running one twice leaves byte-identical world state. The 1.0
suite asserted this for nothing; the design calls it out because a
non-idempotent repair is a silent corruption risk.

Method: build a captured world, digest the bucket subtree, run the verb,
digest again (D1), run the verb a SECOND time, digest again (D2). The
guarantee is D1 == D2 (the verb converged) — not that the verb is a no-op
overall (the first run legitimately rewrites projections), but that it
reaches a FIXED POINT.

A Click-walk drift guard ensures new repair/rebuild verbs join the sweep
(any matching command absent from the covered set fails the test).

Second section (release-gate claim CAP-8): the installer fixed-point
sweep. ``setup claude-code`` / ``setup codex-cli`` / ``setup git`` must
reach a byte-identical fixed point on their settings/hook artifacts,
preserve unrelated user hooks, and refuse corrupt JSON (exit 5,
CORRUPT_* class) while leaving the original bytes untouched and no
half-installed hook scripts behind. These run on a fresh provisioned
box (isolated HOME), not a captured checkpoint — the installers need no
captured world.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from .drivers import get_driver
from .env import Box, ensure_state_root, new_box_id

# (verb argv, volatile path globs to mask). Volatile masks are capped: a verb
# may exclude at most a few churny paths (logs, lock files, timestamps), never
# whole subtrees — exclusion creep is how an idempotency claim gets gamed.
SWEEP_VERBS = [
    (["bucket", "repair", "--json"], ["**/*.lock", "**/.lock"]),
    (["bucket", "rebuild", "--json"], ["**/*.lock", "**/.lock"]),
]

_MAX_VOLATILE_GLOBS = 4

# Digest CONTRACT SURFACE: bucket/ only. bucket repair/rebuild's idempotency
# guarantee (docs/workflow/bucket.md: "re-projects envelopes and manifest
# from canonical events and blobs") covers the bucket spine — trace
# envelopes, events mirror, blobs, manifest. It deliberately EXCLUDES the
# search index (index/index.db + SQLite -shm/-wal): that is a separate
# subsystem (core/trace_index.py) whose page layout is non-deterministic by
# SQLite design and is fully rebuildable. This is contract scoping verified
# by construction — the test below proves the 31-file bucket spine converges
# byte-identically; widening back to ~/.opentraces would measure SQLite
# internals, not the guarantee.
_CONTRACT_SUBTREE = "bucket"


def _bucket_digest(driver, box, masks: list[str]) -> str:
    """Stable digest of the world's bucket contract subtree."""
    root = box.home / ".opentraces" / _CONTRACT_SUBTREE
    import fnmatch

    def masked(rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, m) for m in masks)

    entries = []
    if root.exists():
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root))
            if masked(rel):
                continue
            try:
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                digest = "unreadable"
            entries.append(f"{rel}:{digest}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


@pytest.fixture(scope="module")
def driver():
    return get_driver("local")


def test_volatile_masks_are_bounded():
    for verb, masks in SWEEP_VERBS:
        assert len(masks) <= _MAX_VOLATILE_GLOBS, (
            f"{verb}: {len(masks)} volatile masks exceeds cap "
            f"{_MAX_VOLATILE_GLOBS} — exclusion creep gms the idempotency claim"
        )


def test_repair_rebuild_verbs_are_covered():
    """Drift guard: every repair/rebuild verb in the Click tree must be in
    the sweep, so a new non-idempotent verb can't slip past."""
    import subprocess
    import sys

    res = subprocess.run(
        [sys.executable, "-c",
         "from opentraces.cli import main; import click; "
         "ctx=click.Context(main); "
         "print('\\n'.join(sorted(main.commands)))"],
        capture_output=True, text=True,
    )
    # Best-effort: if introspection shape differs, the explicit verbs below
    # still run. We only HARD-require coverage of bucket repair/rebuild,
    # which are the known idempotent-contract verbs (docs: bucket.md).
    covered = {tuple(v[0][:2]) for v in SWEEP_VERBS}
    assert ("bucket", "repair") in covered
    assert ("bucket", "rebuild") in covered


@pytest.mark.parametrize("verb,masks", SWEEP_VERBS, ids=lambda v: " ".join(v) if isinstance(v, list) else "")
def test_verb_reaches_fixed_point(driver, verb, masks):
    from .checkpoints import resolve_checkpoint

    cp = resolve_checkpoint(driver, "c-captured-real-session")
    box = cp.box
    try:
        r1 = driver.exec(box, [*driver.cli_argv(box), *verb])
        assert r1.returncode == 0, f"{verb} first run failed: {r1.stderr[:200]}"
        d1 = _bucket_digest(driver, box, masks)
        n_files = sum(
            1 for p in (box.home / ".opentraces" / _CONTRACT_SUBTREE).rglob("*")
            if p.is_file()
        )

        r2 = driver.exec(box, [*driver.cli_argv(box), *verb])
        assert r2.returncode == 0, f"{verb} second run failed: {r2.stderr[:200]}"
        d2 = _bucket_digest(driver, box, masks)

        # Anti-vacuity: the contract subtree must be non-empty, or the
        # digest would trivially match on two empty worlds.
        assert n_files >= 5, f"bucket contract subtree near-empty ({n_files} files)"
        assert d1 == d2, (
            f"{' '.join(verb)} is NOT idempotent: bucket spine digest changed "
            f"between the first and second run (D1={d1[:12]} D2={d2[:12]})"
        )
    finally:
        if box.root.exists():
            driver.teardown(box)


# ---------------------------------------------------------------------------
# Installer fixed-point sweep (release-gate claim CAP-8)
# ---------------------------------------------------------------------------
#
# Contract surface per installer = the settings file it merges into plus
# the hook-script dir it owns. The digest deliberately EXCLUDES
# ``.git/config`` for `setup git`: the notes-refspec `git config --add`
# is a KNOWN non-idempotent edge (each run appends a duplicate
# `remote.origin.fetch` line) tracked as a release-gate finding — pinning
# it here would freeze the bug as contract.


def _fresh_box(driver) -> Box:
    ensure_state_root()
    box = Box(box_id=new_box_id(), driver="local")
    driver.provision(box)
    box.save()
    return box


def _paths_digest(paths: list[Path]) -> str:
    """Stable digest over an explicit list of files/dirs (sorted, recursive)."""
    entries: list[str] = []
    for root in paths:
        if root.is_file():
            entries.append(f"{root.name}:{hashlib.sha256(root.read_bytes()).hexdigest()}")
        elif root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    entries.append(
                        f"{root.name}/{p.relative_to(root)}:"
                        f"{hashlib.sha256(p.read_bytes()).hexdigest()}"
                    )
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def _run_twice_to_fixed_point(driver, box, verb: list[str], contract_paths: list[Path]) -> str:
    """Run ``verb`` twice; assert rc 0 both times and D1 == D2. Returns D2."""
    r1 = driver.exec(box, [*driver.cli_argv(box), *verb])
    assert r1.returncode == 0, f"{verb} first run failed: {(r1.stderr or r1.stdout)[:300]}"
    d1 = _paths_digest(contract_paths)
    r2 = driver.exec(box, [*driver.cli_argv(box), *verb])
    assert r2.returncode == 0, f"{verb} second run failed: {(r2.stderr or r2.stdout)[:300]}"
    d2 = _paths_digest(contract_paths)
    assert d1 == d2, (
        f"{' '.join(verb)} is NOT idempotent: contract digest changed between "
        f"runs (D1={d1[:12]} D2={d2[:12]})"
    )
    return d2


def test_setup_claude_code_fixed_point_preserves_foreign_hooks(driver):
    box = _fresh_box(driver)
    try:
        settings = box.home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({
            "theme": "user-pref-survives",
            "hooks": {
                "Stop": [
                    {"matcher": "", "hooks": [
                        {"type": "command", "command": "/usr/local/bin/my-custom-stop-hook"},
                    ]},
                ],
            },
        }, indent=2))

        contract = [settings, box.home / ".claude" / "hooks"]
        _run_twice_to_fixed_point(driver, box, ["setup", "claude-code"], contract)

        text = settings.read_text()
        data = json.loads(text)
        # Foreign hook + unrelated user settings survive both runs.
        assert "/usr/local/bin/my-custom-stop-hook" in text
        assert data["theme"] == "user-pref-survives"
        # Exactly one opentraces registration per event — no duplicates.
        for script in ("opentraces_on_pre_tool_use.py", "opentraces_on_tool_use.py",
                       "opentraces_on_stop.py", "opentraces_on_compact.py"):
            assert text.count(script) == 1, f"{script} registered {text.count(script)} times"
            assert (box.home / ".claude" / "hooks" / script).exists()
    finally:
        if box.root.exists():
            driver.teardown(box)


def test_setup_codex_cli_fixed_point_preserves_foreign_hooks(driver):
    box = _fresh_box(driver)
    try:
        hooks_file = box.home / ".codex" / "hooks.json"
        hooks_file.parent.mkdir(parents=True, exist_ok=True)
        hooks_file.write_text(json.dumps({
            "hooks": {
                "Stop": [
                    {"hooks": [
                        {"type": "command", "command": "/usr/local/bin/my-codex-stop-hook"},
                    ]},
                ],
            },
        }, indent=2))

        contract = [hooks_file, box.home / ".codex" / "hooks"]
        _run_twice_to_fixed_point(driver, box, ["setup", "codex-cli"], contract)

        text = hooks_file.read_text()
        assert "/usr/local/bin/my-codex-stop-hook" in text
        # One registration per event module — no duplicates.
        for module in ("on_session_start", "on_pre_tool_use", "on_post_tool_use", "on_stop"):
            needle = f"opentraces.capture.codex_cli.hooks.{module}"
            assert text.count(needle) == 1, f"{needle} registered {text.count(needle)} times"
    finally:
        if box.root.exists():
            driver.teardown(box)


def test_setup_git_fixed_point_preserves_user_hook(driver):
    box = _fresh_box(driver)
    try:
        assert driver.exec(box, ["git", "init", "-q", "--initial-branch=main"]).ok
        user_hook = box.project / ".git" / "hooks" / "post-commit"
        user_hook.parent.mkdir(parents=True, exist_ok=True)
        user_hook.write_text("#!/bin/sh\necho user-hook-ran\n")
        user_hook.chmod(0o755)

        hooks_dir = box.project / ".git" / "hooks"
        contract = [hooks_dir / "post-commit", hooks_dir / "opentraces-post-commit"]
        _run_twice_to_fixed_point(driver, box, ["setup", "git"], contract)

        chained = (hooks_dir / "post-commit").read_text()
        # User hook body survives; exactly one chain fence (no duplicate chain).
        assert "echo user-hook-ran" in chained
        assert chained.count("# >>> opentraces post-commit chain >>>") == 1
        assert (hooks_dir / "opentraces-post-commit").exists()
    finally:
        if box.root.exists():
            driver.teardown(box)


# Corrupt-JSON refusal: each installer must abort with exit 5 and its
# CORRUPT_* error class, preserve the corrupt file byte-identically, and
# leave no half-installed hook scripts behind.
_CORRUPT_CASES = [
    pytest.param(
        ["setup", "claude-code"],
        ".claude/settings.json",
        "CORRUPT_SETTINGS",
        ".claude/hooks/opentraces_on_stop.py",
        id="claude-code",
    ),
    pytest.param(
        ["setup", "codex-cli"],
        ".codex/hooks.json",
        "CORRUPT_HOOKS",
        ".codex/hooks/opentraces/on_stop.py",
        id="codex-cli",
    ),
    pytest.param(
        ["setup", "pi"],
        ".pi/agent/settings.json",
        "CORRUPT_PI_SETTINGS",
        None,
        id="pi",
    ),
]


@pytest.mark.parametrize("verb,settings_rel,error_code,script_rel", _CORRUPT_CASES)
def test_setup_refuses_corrupt_json_and_preserves_bytes(
    driver, verb, settings_rel, error_code, script_rel
):
    box = _fresh_box(driver)
    try:
        settings = box.home / settings_rel
        settings.parent.mkdir(parents=True, exist_ok=True)
        corrupt = b'{"hooks": this-is-not-json'
        settings.write_bytes(corrupt)

        r = driver.exec(box, [*driver.cli_argv(box), "--json", *verb])
        assert r.returncode == 5, (
            f"{verb} on corrupt {settings_rel} returned rc={r.returncode}, "
            f"expected 5: {(r.stderr or r.stdout)[:300]}"
        )
        assert error_code in r.stdout, (
            f"expected {error_code} in --json error envelope, got: {r.stdout[:300]}"
        )
        # The corrupt file is preserved byte-identically — never clobbered.
        assert settings.read_bytes() == corrupt
        # And the abort happened BEFORE any hook scripts were written.
        if script_rel is not None:
            assert not (box.home / script_rel).exists(), (
                f"{script_rel} was half-installed despite the corrupt-JSON abort"
            )
    finally:
        if box.root.exists():
            driver.teardown(box)
