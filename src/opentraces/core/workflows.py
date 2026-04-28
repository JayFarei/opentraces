"""Local workflow skill registry for Plan 57 datasets."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import paths

_WORKFLOW_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class WorkflowPackage:
    name: str
    path: Path
    digest: str
    description: str | None = None


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
    if template != "default":
        raise ValueError(f"unknown workflow template: {template}")

    destination = workflows_dir() / name
    if destination.exists():
        if not replace:
            raise FileExistsError(f"workflow already exists: {name}")
        shutil.rmtree(destination)

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


def install_workflow(source: Path, *, replace: bool = False) -> WorkflowPackage:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"workflow source is not a directory: {source}")
    source_skill = source / "SKILL.md"
    if not source_skill.exists():
        raise ValueError(f"workflow source has no SKILL.md: {source}")

    metadata = _read_skill_frontmatter(source_skill)
    name = validate_workflow_name(str(metadata.get("name") or source.name))
    destination = workflows_dir() / name
    if destination.exists():
        if not replace:
            raise FileExistsError(f"workflow already exists: {name}")
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    return load_workflow(name)


def load_workflow(name: str) -> WorkflowPackage:
    validate_workflow_name(name)
    path = workflows_dir() / name
    skill_path = path / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"workflow not found: {name}")
    metadata = _read_skill_frontmatter(skill_path)
    return WorkflowPackage(
        name=str(metadata.get("name") or name),
        description=_optional_str(metadata.get("description")),
        path=path,
        digest=compute_workflow_digest(path),
    )


def list_workflows() -> list[WorkflowPackage]:
    root = workflows_dir()
    if not root.exists():
        return []
    packages: list[WorkflowPackage] = []
    for item in sorted(root.iterdir(), key=lambda p: p.name):
        if item.is_dir() and (item / "SKILL.md").exists():
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
    if not (path / "SKILL.md").exists():
        raise ValueError(f"workflow package has no SKILL.md: {path}")
    digest = hashlib.sha256()
    for file_path in _workflow_files(path):
        relative = file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _workflow_files(path: Path) -> list[Path]:
    files = [
        file_path
        for file_path in path.rglob("*")
        if file_path.is_file() and not any(part.startswith(".") for part in file_path.relative_to(path).parts)
    ]
    files.sort(key=lambda file_path: file_path.relative_to(path).as_posix())
    return files


def _read_skill_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    raw = text[4:end]
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


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
        "  - ot trace map\n"
        "  - ot trace get\n"
        "---\n\n"
        f"# {name}\n\n"
        "You are running inside `ot dataset run`. Read the dataset schema from "
        "the run packet, inspect bounded CandidatePackets and Trace Map slices, "
        "and emit plain JSONL rows matching the dataset schema to "
        "`$OT_DATASET_OUTPUT`.\n"
    )
