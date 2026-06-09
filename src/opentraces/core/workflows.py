"""Local workflow skill registry for Plan 57 datasets."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from opentraces_schema import WorkflowSecurityContract

from . import paths

_WORKFLOW_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_BUILTIN_TEMPLATE_PACKAGE = "opentraces.workflow_templates"


@dataclass(frozen=True)
class WorkflowPackage:
    name: str
    path: Path
    digest: str
    description: str | None = None
    entrypoint: Path | None = None
    source_type: str = "package"
    security: WorkflowSecurityContract | None = None


def workflows_dir() -> Path:
    return paths.OPENTRACES_DIR / "workflows"


def validate_workflow_name(name: str) -> str:
    if not name or not _WORKFLOW_NAME_RE.fullmatch(name):
        raise ValueError(
            "workflow name must start with a letter or number and contain only "
            "letters, numbers, '.', '_', or '-'"
        )
    return name


def create_workflow(
    name: str,
    *,
    description: str | None = None,
    template: str = "default",
    replace: bool = False,
) -> WorkflowPackage:
    validate_workflow_name(name)
    destination = workflows_dir() / name
    if destination.exists():
        if not replace:
            raise FileExistsError(f"workflow already exists: {name}")
        shutil.rmtree(destination)

    if template != "default":
        source = _builtin_template(template)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_traversable_tree(source, destination)
        _rewrite_template_metadata(destination, name=name, description=description)
        return load_workflow(name)

    (destination / "examples").mkdir(parents=True)
    (destination / "scripts").mkdir()
    (destination / "tests").mkdir()
    skill_description = description or f"Build dataset rows for {name}"
    (destination / "SKILL.md").write_text(
        _default_skill_text(name, skill_description),
        encoding="utf-8",
    )
    (destination / "examples" / "input-candidate-packet.json").write_text(
        "{\n"
        '  "unit_id": "tu:example:trace",\n'
        '  "trace_id": "example",\n'
        '  "title": "Example candidate"\n'
        "}\n",
        encoding="utf-8",
    )
    (destination / "examples" / "expected-row.json").write_text(
        "{\n"
        '  "source_trace_id": "example",\n'
        '  "source_unit_id": "tu:example:trace",\n'
        '  "summary": "Example row"\n'
        "}\n",
        encoding="utf-8",
    )
    (destination / "tests" / "README.md").write_text(
        "# Workflow tests\n\nAdd helper-script tests here.\n",
        encoding="utf-8",
    )
    return load_workflow(name)


def list_workflow_templates() -> list[str]:
    templates = ["default"]
    root = resources.files(_BUILTIN_TEMPLATE_PACKAGE)
    for item in sorted(root.iterdir(), key=lambda resource: resource.name):
        if item.is_dir() and _traversable_has_workflow_entrypoint(item):
            templates.append(item.name)
    return templates


def install_workflow(source: Path, *, replace: bool = False) -> WorkflowPackage:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"workflow source is not a directory: {source}")
    source_skill = _workflow_entrypoint(source)

    _reject_symlinks(source)

    metadata = _read_skill_frontmatter(source_skill)
    name = validate_workflow_name(str(metadata.get("name") or source.name))
    destination = workflows_dir() / name
    if destination.exists() and not replace:
        raise FileExistsError(f"workflow already exists: {name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".tmp-install-{name}-{uuid.uuid4().hex}"
    shutil.copytree(source, staging, symlinks=False)
    if destination.exists():
        backup = destination.parent / f".tmp-replace-{name}-{uuid.uuid4().hex}"
        os.rename(destination, backup)
        try:
            os.rename(staging, destination)
        except OSError:
            os.rename(backup, destination)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    else:
        os.rename(staging, destination)
    return load_workflow(name)


def _reject_symlinks(source: Path) -> None:
    if source.is_symlink():
        raise ValueError(f"workflow source must not be a symlink: {source}")
    for entry in source.rglob("*"):
        if entry.is_symlink():
            raise ValueError(
                "workflow contains symlink which would dereference outside the "
                f"package on install: {entry.relative_to(source)}"
            )


def load_workflow(name: str) -> WorkflowPackage:
    validate_workflow_name(name)
    path = workflows_dir() / name
    try:
        skill_path = _workflow_entrypoint(path)
    except ValueError:
        raise FileNotFoundError(f"workflow not found: {name}")
    metadata = _read_skill_frontmatter(skill_path)
    return WorkflowPackage(
        name=str(metadata.get("name") or name),
        description=_optional_str(metadata.get("description")),
        path=path,
        digest=compute_workflow_digest(path),
        entrypoint=skill_path,
        source_type="package",
        security=_workflow_security_contract(metadata),
    )


def list_workflows() -> list[WorkflowPackage]:
    root = workflows_dir()
    if not root.exists():
        return []
    packages: list[WorkflowPackage] = []
    for item in sorted(root.iterdir(), key=lambda p: p.name):
        if item.is_dir() and _has_workflow_entrypoint(item):
            packages.append(load_workflow(item.name))
    return packages


def remove_workflow(name: str) -> Path:
    validate_workflow_name(name)
    path = workflows_dir() / name
    if not path.exists():
        raise FileNotFoundError(f"workflow not found: {name}")
    shutil.rmtree(path)
    return path


def compute_workflow_digest(path: Path) -> str:
    path = path.expanduser().resolve()
    digest = hashlib.sha256()
    if path.is_file():
        if path.suffix.lower() not in {".md", ".markdown"}:
            raise ValueError(f"workflow file must be Markdown: {path}")
        files = [path]
        root = path.parent
    elif path.is_dir():
        _workflow_entrypoint(path)
        files = _workflow_files(path)
        root = path
    else:
        raise ValueError(f"workflow path does not exist: {path}")
    for file_path in files:
        relative = file_path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def resolve_workflow_reference(source: str | Path) -> WorkflowPackage:
    """Resolve a Markdown file or workflow package directory.

    This is the dataset-facing path. The legacy registry still resolves
    by installed skill name; direct dataset creation can now pin an
    explicit Markdown file or package folder without an install step.
    """

    path = Path(source).expanduser().resolve()
    if path.is_file():
        if path.suffix.lower() not in {".md", ".markdown"}:
            raise ValueError(f"workflow file must be Markdown: {path}")
        metadata = _read_skill_frontmatter(path)
        name = validate_workflow_name(str(metadata.get("name") or path.stem))
        return WorkflowPackage(
            name=name,
            description=_optional_str(metadata.get("description")),
            path=path,
            digest=compute_workflow_digest(path),
            entrypoint=path,
            source_type="file",
            security=_workflow_security_contract(metadata),
        )
    if path.is_dir():
        entrypoint = _workflow_entrypoint(path)
        metadata = _read_skill_frontmatter(entrypoint)
        name = validate_workflow_name(str(metadata.get("name") or path.name))
        return WorkflowPackage(
            name=name,
            description=_optional_str(metadata.get("description")),
            path=path,
            digest=compute_workflow_digest(path),
            entrypoint=entrypoint,
            source_type="package",
            security=_workflow_security_contract(metadata),
        )
    raise FileNotFoundError(f"workflow path not found: {source}")


def _workflow_files(path: Path) -> list[Path]:
    files = [
        file_path
        for file_path in path.rglob("*")
        if file_path.is_file() and not any(part.startswith(".") for part in file_path.relative_to(path).parts)
    ]
    files.sort(key=lambda file_path: file_path.relative_to(path).as_posix())
    return files


def _builtin_template(name: str) -> Traversable:
    root = resources.files(_BUILTIN_TEMPLATE_PACKAGE)
    template = root.joinpath(name)
    if template.is_dir() and _traversable_has_workflow_entrypoint(template):
        return template
    known = ", ".join(list_workflow_templates())
    raise ValueError(f"unknown workflow template: {name} (available: {known})")


def _traversable_has_workflow_entrypoint(path: Traversable) -> bool:
    return path.joinpath("SKILL.md").is_file() or path.joinpath("WORKFLOW.md").is_file()


def _copy_traversable_tree(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            _copy_traversable_tree(item, target)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.read_bytes())


def _rewrite_template_metadata(
    destination: Path,
    *,
    name: str,
    description: str | None,
) -> None:
    entrypoint = _workflow_entrypoint(destination)
    text = entrypoint.read_text(encoding="utf-8")
    old_metadata = _read_skill_frontmatter(entrypoint)
    old_name = old_metadata.get("name")
    if not text.startswith("---\n"):
        return
    end = text.find("\n---", 4)
    if end == -1:
        return
    raw = text[4:end]
    lines = raw.splitlines()
    fields = {"name": name}
    if description is not None:
        fields["description"] = description
    seen: set[str] = set()
    rewritten: list[str] = []
    for line in lines:
        key, sep, _value = line.partition(":")
        normalized_key = key.strip()
        if sep and normalized_key in fields:
            rewritten.append(f"{normalized_key}: {fields[normalized_key]}")
            seen.add(normalized_key)
        else:
            rewritten.append(line)
    for key, value in fields.items():
        if key not in seen:
            rewritten.insert(0 if key == "name" else len(rewritten), f"{key}: {value}")
    updated = "---\n" + "\n".join(rewritten) + text[end:]
    if old_name and old_name != name:
        updated = re.sub(
            rf"^# {re.escape(old_name)}\s*$",
            f"# {name}",
            updated,
            count=1,
            flags=re.MULTILINE,
        )
    entrypoint.write_text(updated, encoding="utf-8")
    if old_name and old_name != name:
        _replace_text_in_workflow_files(destination, old_name, name)


def _replace_text_in_workflow_files(destination: Path, old: str, new: str) -> None:
    for path in _workflow_files(destination):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if old not in text:
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")


def _has_workflow_entrypoint(path: Path) -> bool:
    return (path / "SKILL.md").exists() or (path / "WORKFLOW.md").exists()


def _workflow_entrypoint(path: Path) -> Path:
    workflow_path = path / "WORKFLOW.md"
    skill_path = path / "SKILL.md"
    if workflow_path.exists():
        return workflow_path
    if skill_path.exists():
        return skill_path
    raise ValueError(f"workflow package has no WORKFLOW.md or SKILL.md: {path}")


def _read_skill_frontmatter(path: Path) -> dict[str, Any]:
    """Parse a SKILL.md / WORKFLOW.md YAML front matter block.

    Uses ``yaml.safe_load`` so nested blocks (e.g. a ``security:`` contract or a
    ``requires:`` list) parse into real structures. Falls back to the legacy
    flat ``key: value`` reader if the block is not valid YAML, so any front
    matter that parsed before keeps parsing (back-compatible).
    """

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    raw = text[4:end]
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        loaded = None
    if isinstance(loaded, dict):
        return loaded
    return _legacy_flat_frontmatter(raw)


def _legacy_flat_frontmatter(raw: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


def _workflow_security_contract(metadata: dict[str, Any]) -> WorkflowSecurityContract | None:
    """Build the workflow security contract from front matter, if declared.

    Resilient by design: a present-but-malformed ``security:`` block (a scalar
    typo, an unknown tool, a forbidden extra key) must not crash workflow
    loading — ``load_workflow`` is called for every package by
    ``list_workflows()``, so one bad workflow would otherwise break the whole
    listing. A malformed block degrades to no contract with a warning; callers
    that need to enforce a contract (``dataset new``) surface the absence.
    """

    if "security" not in metadata:
        return None
    raw = metadata.get("security")
    if not isinstance(raw, dict):
        _warn_bad_security_contract(
            f"expected a mapping/block, got {type(raw).__name__}"
        )
        return None
    try:
        return WorkflowSecurityContract.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — never break workflow loading
        _warn_bad_security_contract(str(exc))
        return None


def _warn_bad_security_contract(detail: str) -> None:
    import sys

    print(
        f"warning: ignoring invalid workflow 'security' contract ({detail})",
        file=sys.stderr,
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _default_skill_text(name: str, description: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "mode: agent-skill\n"
        "requires:\n"
        "  - ot trace query\n"
        "  - ot trace slice\n"
        "  - ot trace map\n"
        "  - ot trace get\n"
        "---\n\n"
        f"# {name}\n\n"
        "You are running inside `ot dataset run`. Read the dataset schema from "
        "the run packet, inspect bounded CandidatePackets and Trace Map slices, "
        "and emit plain JSONL rows matching the dataset schema to "
        "`$OT_DATASET_OUTPUT`.\n"
    )
