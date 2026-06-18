"""Select / reconcile the active opentraces runtime on multi-install machines.

Issue #99. On a machine where opentraces is installed several ways at once
(pipx + Homebrew + an editable checkout), the integration glue (Claude/Codex
hooks, the git post-commit shim, the watcher worker shim) can each execute
opentraces from a DIFFERENT install root. #93 added the DETECTION
(``doctor.runtime_provenance``); this module is the TREATMENT:

* :func:`discover_runtimes` — a thin presenter over #93's provenance (never
  recomputes it).
* :func:`apply_runtime` — re-render the DISCOVERED integration runners so they
  execute from a chosen install root (``use <kind>``) or the editable checkout
  (``use-dev``). Integrations-only, dry-run-able, JSON-reportable.
* :func:`removal_command_for` / :func:`remove_duplicates` — PRINT the data-safe
  package-removal commands for the duplicate installs. Never executes them.

Load-bearing data-safety invariants (mirrored from the plan):

* NEVER touches ``~/.opentraces`` bucket / datasets / staging, and NEVER a Git
  ref. The only file this module WRITES outside the integration glue is the
  project-local ``<project>/.opentraces/runtime_selection.json`` dev marker.
* Package uninstall is PRINTED, never executed (no ``subprocess`` for a package
  manager anywhere in this module).
* The chosen interpreter is threaded into the renderers via
  ``capture._interpreter.selected_interpreter`` so the #86 Cellar→opt remap and
  #65 run-time watcher resolution invariants survive selection.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# user-facing kind aliases → canonical source_kind used by the classifier.
_KIND_ALIASES = {"homebrew": "brew", "brew": "brew", "pipx": "pipx", "source": "source"}
SUPPORTED_KINDS = ("pipx", "homebrew", "brew", "source")

# provenance runner name → re-render target. ``otlp`` is intentionally excluded
# from runtime selection in v1 (see integration_repair).
_EXCLUDED_RUNNERS = {"otlp", "capture-otlp"}


def normalize_kind(kind: str) -> str | None:
    """Canonicalise a user-facing kind to a source_kind, or None if unknown."""
    return _KIND_ALIASES.get((kind or "").strip().lower())


def _registry_key(name: str | None) -> str | None:
    if not name:
        return None
    if name == "watcher":
        return "watcher"
    try:
        from ..capture import _registry_key as _rk

        return _rk(name)
    except Exception:  # noqa: BLE001
        return name


def discover_runtimes(cwd: Path | None = None, *, probe: bool = False) -> dict[str, Any]:
    """Return #93's runtime provenance verbatim (never recompute it)."""
    from .doctor import _safe_runtime_provenance

    cwd = Path(cwd or Path.cwd())
    return _safe_runtime_provenance(cwd, probe=probe)


def _realpath(p: str | None) -> str | None:
    if not p:
        return None
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def _abspath_no_realpath(p: str | os.PathLike[str] | None) -> str | None:
    """Absolute path normalization that preserves venv symlink identity."""
    if not p:
        return None
    return os.path.abspath(os.path.expanduser(os.fspath(p)))


# ---------------------------------------------------------------------------
# target-interpreter resolution (Fix B.5 — hardened for real machines)
# ---------------------------------------------------------------------------
def resolve_target_interpreter(
    prov: dict[str, Any], kind: str
) -> tuple[str | None, str | None]:
    """Resolve a ``use <kind>`` request to a concrete interpreter path.

    Returns ``(interpreter, error)``. Resolution order (honest about the
    path-heuristic fragility called out in finding #7):

    1. a ``discovered_installs`` entry whose ``source_kind == kind`` and which
       is ``verified`` (probed module file);
    2. an unverified ``discovered_installs`` entry whose ``source_kind == kind``
       (path-classified);
    3. a fallback scan of ``integration_runners`` interpreters whose
       path-classified kind matches.

    When nothing resolves, returns ``(None, <guidance naming --probe-runtimes>)``
    so the CLI can exit 2 with an actionable message.
    """
    from .doctor import _classify_source_kind

    canonical = normalize_kind(kind)
    if canonical is None:
        return None, f"unknown runtime kind {kind!r}; expected one of {SUPPORTED_KINDS}"

    installs = prov.get("discovered_installs") or []
    candidate: str | None = None
    # Candidates are taken AS-IS (NOT realpath-collapsed). #86/codex: a live
    # Homebrew opentraces venv interpreter
    # (``.../Cellar/opentraces/<ver>/libexec/bin/python``) realpaths down to the
    # bare ``python@3.x`` — baking THAT loses the opentraces venv. We must hand
    # the un-collapsed path to stable_interpreter so its Cellar→opt remap
    # PRESERVES the opentraces venv identity (``.../opt/opentraces/.../python``).
    # 1 — verified match.
    for entry in installs:
        if entry.get("source_kind") == canonical and entry.get("verified") and entry.get("interpreter"):
            candidate = entry["interpreter"]
            break
    # 2 — unverified path-classified match.
    if candidate is None:
        for entry in installs:
            if entry.get("source_kind") == canonical and entry.get("interpreter"):
                candidate = entry["interpreter"]
                break
    # 3 — runner-interpreter fallback.
    if candidate is None:
        for runner in prov.get("integration_runners") or []:
            interp = runner.get("runner")
            if interp and _classify_source_kind(interp) == canonical:
                candidate = interp
                break

    if candidate is not None:
        # SECURITY / #65 + #86: never re-bake a DELETED or version-pinned-Cellar
        # interpreter (that is exactly the drift this feature must NOT introduce).
        # Route the candidate through stable_interpreter (Cellar→opt remap that
        # PRESERVES the opentraces venv identity), then validate THAT output
        # exists + is executable. A Cellar path with no live opt symlink stays a
        # Cellar path and then fails the existence check → rejected.
        usable, resolved, why = _validate_interpreter(candidate)
        if usable:
            return resolved, None
        return None, (
            f"The {canonical!r} install's interpreter is not usable ({why}): "
            f"{candidate}. `setup runtime use` will not re-bake a missing or "
            "stale interpreter (that would reintroduce the #65/#86 drift it "
            "exists to prevent). Reinstall that runtime or pick another."
        )

    if prov.get("probed"):
        return None, (
            f"No {canonical!r} opentraces install was discovered among the "
            "integration runners. `setup runtime use` only re-renders glue to "
            "an ALREADY-discovered install; it never installs a missing runtime."
        )
    return None, (
        f"No {canonical!r} install resolved. Classification of non-current "
        "installs is a path heuristic by default; retry with `--probe-runtimes` "
        "to verify each runner's real module file before selecting, or check "
        "`setup runtime status` for the discovered installs."
    )


def _validate_interpreter(interpreter: str) -> tuple[bool, str | None, str | None]:
    """Validate + stabilise a target interpreter (security / #65 / #86).

    Returns ``(usable, resolved_interpreter, reason_if_not)``:

    * resolves the interpreter through ``stable_interpreter`` (the #86 Cellar→opt
      remap), then
    * requires the RESOLVED path to exist on disk AND be executable.

    A version-pinned Cellar path whose ``opt`` symlink is gone stays a Cellar
    path after ``stable_interpreter`` and then fails the existence/executable
    check — so it is rejected rather than baked into a hook that would silently
    break on the next ``brew upgrade``.
    """
    from ..capture._interpreter import stable_interpreter

    resolved = stable_interpreter(interpreter)
    p = Path(resolved)
    if not p.is_file():
        return False, None, "interpreter does not exist on disk"
    if not os.access(resolved, os.X_OK):
        return False, None, "interpreter is not executable"
    return True, resolved, None


def resolve_dev_interpreter(
    prov: dict[str, Any], project: Path
) -> tuple[str | None, str | None]:
    """Resolve the editable-checkout interpreter for ``use-dev``."""
    current = prov.get("current") or {}
    candidate: str | None = None
    if current.get("source_kind") == "source":
        # Use the RAW running interpreter (the editable checkout's venv python,
        # e.g. ``<checkout>/.venv/bin/python``) — NOT its realpath, which would
        # resolve the venv symlink onward to the base interpreter whose
        # site-packages lack the editable opentraces (breaking the hook).
        import sys

        candidate = sys.executable or current.get("python")
    if candidate is None:
        # else look for a venv under the project (raw path, not realpath'd).
        project = Path(project)
        for venv in (".venv", ".testvenv", "venv"):
            cand = project / venv / "bin" / "python"
            if cand.is_file():
                candidate = str(cand)
                break
    if candidate is not None and Path(candidate).is_file() and os.access(candidate, os.X_OK):
        return candidate, None
    return None, (
        f"No usable editable-checkout interpreter found. Run `setup runtime "
        f"use-dev` from a source checkout, or create a virtualenv "
        f"({project}/.venv) with opentraces installed editable (`pip install -e .`)."
    )


# ---------------------------------------------------------------------------
# package-removal command (PRINTED, never executed)
# ---------------------------------------------------------------------------
def removal_command_for(
    source_kind: str, *, interpreter: str | None = None
) -> tuple[str, str | None]:
    """Map a ``source_kind`` to a PRINTED package-removal command + optional note.

    Generalises ``core.uninstall.package_uninstall_command`` over an arbitrary
    install (not just the current process). For ``source`` the command is keyed
    to the discovered entry's OWN venv (``<venv>/bin/pip``), because a bare
    ``pip uninstall opentraces`` from the wrong venv is a no-op or removes the
    wrong thing (finding #6). NEVER executed by this function.
    """
    kind = (source_kind or "").strip().lower()
    if kind == "brew":
        return "brew uninstall jayfarei/opentraces/opentraces", None
    if kind == "pipx":
        return "pipx uninstall opentraces", None
    if kind == "source":
        if interpreter:
            import shlex

            bindir = os.path.dirname(interpreter)
            pip = os.path.join(bindir, "pip")
            return (
                f"{shlex.quote(pip)} uninstall opentraces",
                "this is an editable checkout; to fully remove it, run the above "
                "in its venv and delete the checkout tree.",
            )
        return (
            "pip uninstall opentraces  # (run in your editable-checkout venv)",
            "this is an editable checkout; run the above in its venv.",
        )
    return "pip uninstall opentraces", None


def remove_duplicates(
    cwd: Path | None = None, *, keep: str | None = None, probe: bool = False
) -> dict[str, Any]:
    """Build the PRINT-ONLY removal plan for duplicate installs.

    Returns ``{keep, duplicates:[{source_kind, interpreter, command, note}]}``.
    NEVER shells out. Excludes the kept runtime and never touches data.
    """
    prov = discover_runtimes(cwd, probe=probe)
    current = prov.get("current") or {}
    keep_canonical = normalize_kind(keep) if keep else (current.get("source_kind") or None)

    duplicates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in prov.get("discovered_installs") or []:
        kind = entry.get("source_kind") or "unknown"
        interp = entry.get("interpreter")
        if keep_canonical is not None and kind == keep_canonical:
            continue
        key = f"{kind}:{interp}"
        if key in seen:
            continue
        seen.add(key)
        command, note = removal_command_for(kind, interpreter=interp)
        duplicates.append({
            "source_kind": kind,
            "interpreter": interp,
            "command": command,
            "note": note,
        })

    return {
        "status": "ok",
        "action": "remove-duplicates",
        "keep": keep_canonical,
        "duplicates": duplicates,
        "executed": False,
        "data_safety": (
            "removal commands are PRINTED only; nothing was executed and no "
            "captured data, datasets, staging, or git refs were touched."
        ),
    }


# ---------------------------------------------------------------------------
# dev-runtime marker (Mode 2)
# ---------------------------------------------------------------------------
def write_dev_marker(cwd: Path, interpreter: str, project: Path) -> Path:
    import json as _json

    from .doctor import runtime_selection_marker_path

    path = runtime_selection_marker_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    marker_interpreter = _abspath_no_realpath(interpreter) or interpreter
    path.write_text(
        _json.dumps(
            {
                "mode": "dev",
                "interpreter": marker_interpreter,
                "interpreter_realpath": _realpath(marker_interpreter),
                "project": _abspath_no_realpath(project) or str(project),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def clear_dev_marker(cwd: Path) -> bool:
    from .doctor import runtime_selection_marker_path

    path = runtime_selection_marker_path(cwd)
    if path.is_file():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# the core capability — re-render discovered runners to a chosen interpreter
# ---------------------------------------------------------------------------
def _discovered_runner_keys(prov: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for runner in prov.get("integration_runners") or []:
        name = runner.get("name")
        if name in _EXCLUDED_RUNNERS:
            continue
        key = _registry_key(name)
        if key:
            keys.add(key)
    return keys


def apply_runtime(
    cwd: Path | None = None,
    *,
    kind: str | None = None,
    dev: bool = False,
    project: Path | None = None,
    dry_run: bool = False,
    probe: bool = False,
) -> dict[str, Any]:
    """Re-render the discovered integration runners to a chosen runtime.

    Exactly one of ``kind`` (``use <kind>``) or ``dev=True`` (``use-dev``) must
    be set. Returns a JSON-able envelope. On a resolution failure returns an
    ``error`` envelope (the CLI maps that to exit 2). Integrations-only and
    data-safe by construction.
    """
    cwd = Path(cwd or Path.cwd())
    project = Path(project or cwd)
    prov = discover_runtimes(cwd, probe=probe)

    if dev:
        action = "use-dev"
        runtime_label = "dev"
        target, err = resolve_dev_interpreter(prov, project)
    else:
        action = "use"
        runtime_label = normalize_kind(kind or "") or (kind or "")
        target, err = resolve_target_interpreter(prov, kind or "")

    if target is None:
        return {
            "status": "error",
            "action": action,
            "runtime": runtime_label,
            "resolved_interpreter": None,
            "dry_run": dry_run,
            "integrations_only": True,
            "error": {"code": "RUNTIME_NOT_FOUND", "kind": action, "message": err},
        }

    only = _discovered_runner_keys(prov)

    from .integration_repair import repair_installed_integrations

    repair = repair_installed_integrations(
        cwd, interpreter=target, dry_run=dry_run, only=only
    )

    dev_marker: str | None = None
    if not dry_run:
        if dev:
            dev_marker = str(write_dev_marker(cwd, target, project))
        else:
            # Switching to an installed release clears any deliberate dev marker.
            clear_dev_marker(cwd)

    envelope: dict[str, Any] = {
        "status": "ok" if not repair.get("errors") else "warning",
        "action": action,
        "runtime": runtime_label,
        "resolved_interpreter": target,
        "dry_run": dry_run,
        "integrations_only": True,
        "runners_targeted": sorted(only),
        "repaired": repair.get("repaired", []),
        "skipped": repair.get("skipped", []),
        "errors": repair.get("errors", []),
        "dev_marker": dev_marker,
        "data_safety": (
            "integrations-only re-render; no captured data, datasets, staging, "
            "or git refs were touched, and no package was installed or removed."
        ),
    }
    if dry_run:
        envelope["plan"] = repair.get("plan", [])
    return envelope
