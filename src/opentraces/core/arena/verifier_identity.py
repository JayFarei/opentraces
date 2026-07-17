"""Canonical, importable identity for stored bench verifiers.

Candidates prefer deepest contiguous real-package ancestry, with equally
specific aliases refused.  Origins are compared by inode, and coordinate scans
follow Python's package-before-module precedence.  Both canonical names and the
``__module__`` fallback must resolve unambiguously to the callable's source.

Known limitations (v0, accepted).  What is guaranteed: the recorded identity
imports to exactly this source file by inode identity ``(st_dev, st_ino)``; all
realistic ambiguity (same-name shadows, first-level and nested-within-ancestor
symlink aliases, case-fold ``sys.path`` spellings, package-over-module
precedence, stale-cache shadows) fails closed; and the source is digest-pinned
and re-verified at reverify.  What is not guaranteed: a second canonical import
name that reaches the *same* source is not always enumerated when the alias is
created by a cross-tree symlink bridge (the ``sys.path`` root or an intermediate
wrapper directory is not itself a source ancestor) or by a hardlink (one source
inode with directory entries in two packages).  In those same-source cases the
"multiple canonical import paths" refusal may be skipped.  This cannot cause a
wrong-source verdict: the resolver still resolves to the correct source inode,
and the digest is re-checked at reverify.
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


def _origin_key(path: Path) -> tuple[int, int] | None:
    """Return the filesystem identity of an origin, following symlinks."""

    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_dev, stat.st_ino


def _relative_source_path(directory: Path, source: Path) -> Path | None:
    """Return source relative to a directory spelling with the same inode."""

    directory_key = _origin_key(directory)
    if directory_key is None:
        return None
    for parent in source.parents:
        if _origin_key(parent) == directory_key:
            return source.relative_to(parent)
    return None


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
    source_key = _origin_key(source)
    if source_key is None or _origin_key(root / relative) != source_key:
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
    relative = _relative_source_path(root, source)
    if relative is not None:
        candidate = _candidate_from_relative(root, relative, source)
        if candidate is not None:
            candidates.append(candidate)

    source_ancestor_keys = {
        key for parent in source.parents if (key := _origin_key(parent)) is not None
    }
    root_key = _origin_key(root)
    if root_key is None or root_key not in source_ancestor_keys:
        return candidates

    def walk_package_spellings(
        directory: Path,
        parts: tuple[str, ...],
        ancestor_keys: frozenset[tuple[int, int]],
    ) -> None:
        try:
            entries = os.scandir(directory)
        except OSError:
            return
        with entries:
            for entry in entries:
                if not entry.name.isidentifier() or not entry.is_dir(
                    follow_symlinks=True
                ):
                    continue
                package_path = Path(entry.path)
                package_key = _origin_key(package_path)
                if (
                    package_key is None
                    or package_key not in source_ancestor_keys
                    or package_key in ancestor_keys
                ):
                    continue
                package_parts = (*parts, entry.name)
                if not parts and not (package_path / "__init__.py").is_file():
                    continue
                alias_relative = _relative_source_path(package_path, source)
                if alias_relative is not None:
                    candidate = _candidate_from_relative(
                        root,
                        Path(*package_parts) / alias_relative,
                        source,
                    )
                    if candidate is not None:
                        candidates.append(candidate)
                walk_package_spellings(
                    package_path,
                    package_parts,
                    ancestor_keys | {package_key},
                )

    walk_package_spellings(root, (), frozenset({root_key}))
    return candidates


def _coordinate_origins(candidate: str) -> list[Path]:
    """Inventory real-file origins for a dotted coordinate without importing."""

    parts = candidate.split(".")
    origins: list[Path] = []
    for root in _sys_path_roots():
        package_path = root
        for index, part in enumerate(parts[:-1]):
            package_path /= part
            if not package_path.is_dir():
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
                origins.append(possible_origin)
                break
    return origins


def _require_unambiguous_coordinate(candidate: str, source: Path) -> None:
    origins = _coordinate_origins(candidate)
    source_key = _origin_key(source)
    origin_keys = [_origin_key(origin) for origin in origins]
    if (
        source_key is None
        or not origin_keys
        or any(key is None for key in origin_keys)
        or origin_keys[0] != source_key
        or set(origin_keys) != {source_key}
    ):
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
    module_name = _canonical_module_from_source(source)
    used_fallback = module_name is None
    if used_fallback:
        module_name = verifier.__module__
    if not isinstance(module_name, str) or not module_name:
        raise VerifierIdentityError("verifier has no module identity")
    if used_fallback:
        _require_unambiguous_coordinate(module_name, source)
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
    source_key = _origin_key(source)
    target_source_key = (
        _origin_key(target_source) if target_source is not None else None
    )
    if not callable(target) or source_key is None or target_source_key != source_key:
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
