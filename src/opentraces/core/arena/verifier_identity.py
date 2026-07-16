"""Canonical, importable identity for stored bench verifiers.

Candidates rank by deepest contiguous real-package ancestry, then dotted-path
length.  Different-depth names for the same source therefore prefer the deeper
package, while equal-specificity aliases are ambiguous.  The selected name must
also resolve to only that source across filesystem ``sys.path`` roots; an
earlier shadow or any distinct origin fails closed.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VerifierIdentityError(ValueError):
    """A verifier cannot be named and imported for stored-only replay."""

    code = "verifier_identity_invalid"


@dataclass(frozen=True, slots=True)
class ResolvedVerifier:
    """Canonical imported callable and the identity bound to its source bytes."""

    target: Callable[..., Any]
    name: str
    digest: str


def _sys_path_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw_root in sys.path:
        root = Path(raw_root or Path.cwd()).absolute()
        if root in seen:
            continue
        seen.add(root)
        roots.append(root)
    return roots


def _candidate_from_relative(
    root: Path,
    relative: Path,
    source: Path,
) -> tuple[str, tuple[int, int]] | None:
    if relative.suffix != ".py":
        return None
    parts = list(relative.with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    if not parts or any(not part.isidentifier() for part in parts):
        return None
    try:
        if (root / relative).resolve() != source:
            return None
    except OSError:
        return None

    package_depth = 0
    package_path = root
    package_parts = parts if is_package else parts[:-1]
    if not package_parts or not (root / package_parts[0] / "__init__.py").is_file():
        return None
    for part in package_parts:
        package_path /= part
        if not (package_path / "__init__.py").is_file():
            break
        package_depth += 1
    return ".".join(parts), (package_depth, len(parts))


def _source_candidates_for_root(
    root: Path,
    source: Path,
) -> list[tuple[str, tuple[int, int]]]:
    candidates: list[tuple[str, tuple[int, int]]] = []
    direct_first: str | None = None
    try:
        relative = source.relative_to(root.resolve())
    except (OSError, ValueError):
        relative = None
    if relative is not None:
        direct_first = relative.parts[0]
        candidate = _candidate_from_relative(root, relative, source)
        if candidate is not None:
            candidates.append(candidate)

    # Preserve first-level symlink spellings instead of deriving names only from
    # the real path.  Non-symlink entries cannot add an alias beyond the direct
    # first component, so they are skipped to keep site-packages scans cheap.
    try:
        entries = os.scandir(root)
    except OSError:
        return candidates
    with entries:
        for entry in entries:
            if entry.name != direct_first and not entry.is_symlink():
                continue
            if not entry.name.isidentifier():
                continue
            package_path = Path(entry.path)
            if not (package_path / "__init__.py").is_file():
                continue
            try:
                alias_relative = source.relative_to(package_path.resolve())
            except (OSError, ValueError):
                continue
            candidate = _candidate_from_relative(
                root,
                Path(entry.name) / alias_relative,
                source,
            )
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _coordinate_origins(candidate: str) -> list[Path]:
    """Inventory real-file origins for a dotted coordinate without importing."""

    parts = candidate.split(".")
    origins: list[Path] = []
    for root in _sys_path_roots():
        package_path = root
        for index, part in enumerate(parts[:-1]):
            package_path /= part
            if not package_path.is_dir() or (
                index == 0 and not (package_path / "__init__.py").is_file()
            ):
                break
        else:
            leaf = parts[-1]
            # FileFinder checks a regular package before a same-named module.
            possible_origins = (
                package_path / leaf / "__init__.py",
                package_path / f"{leaf}.py",
            )
            for possible_origin in possible_origins:
                if not possible_origin.is_file():
                    continue
                try:
                    origins.append(possible_origin.resolve())
                except OSError:
                    continue
    return origins


def _require_unambiguous_coordinate(candidate: str, source: Path) -> None:
    origins = _coordinate_origins(candidate)
    distinct_origins = set(origins)
    if not origins or origins[0] != source or distinct_origins != {source}:
        raise VerifierIdentityError(
            "verifier module coordinate is shadowed by a distinct source"
        )


def _canonical_module_from_source(source: Path) -> str | None:
    candidates: dict[str, tuple[int, int]] = {}
    for root in _sys_path_roots():
        for candidate, specificity in _source_candidates_for_root(root, source):
            candidates[candidate] = max(
                candidates.get(candidate, specificity),
                specificity,
            )
    if not candidates:
        return None
    highest_specificity = max(candidates.values())
    preferred = sorted(
        candidate
        for candidate, specificity in candidates.items()
        if specificity == highest_specificity
    )
    if len(preferred) > 1:
        raise VerifierIdentityError("verifier source has multiple canonical import paths")
    candidate = preferred[0]
    _require_unambiguous_coordinate(candidate, source)
    return candidate


def resolve_verifier(verifier: Callable[..., Any]) -> ResolvedVerifier:
    """Resolve untrusted callable metadata to the canonical imported callable."""

    source_value = inspect.getsourcefile(verifier)
    if source_value is None:
        raise VerifierIdentityError("cannot locate verifier source")
    source = Path(source_value).resolve()
    qualname = verifier.__qualname__
    if (
        not isinstance(qualname, str)
        or not qualname
        or "<locals>" in qualname
        or any(not part.isidentifier() for part in qualname.split("."))
    ):
        raise VerifierIdentityError("verifier must be an importable module-level callable")
    module_name = _canonical_module_from_source(source) or verifier.__module__
    if not isinstance(module_name, str) or not module_name:
        raise VerifierIdentityError("verifier has no module identity")
    try:
        target: object = importlib.import_module(module_name)
        for attribute in qualname.split("."):
            target = getattr(target, attribute)
        target_source_value = inspect.getsourcefile(target)
        target_source = (
            Path(target_source_value).resolve() if target_source_value is not None else None
        )
    except (AttributeError, ImportError, ModuleNotFoundError, OSError, TypeError) as exc:
        raise VerifierIdentityError("verifier must be an importable module-level callable") from exc
    if not callable(target) or target_source != source:
        raise VerifierIdentityError("verifier must be an importable module-level callable")
    try:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise VerifierIdentityError("cannot read verifier source") from exc
    return ResolvedVerifier(
        target=target,
        name=f"{module_name}.{qualname}",
        digest=f"sha256:{digest}",
    )


def callable_identity(verifier: Callable[..., Any]) -> tuple[str, str]:
    """Return the stable import coordinate and exact source digest for a callable."""

    resolved = resolve_verifier(verifier)
    return resolved.name, resolved.digest
