"""The workflow -> consumer contract.

opentraces splits "trace data at a place and time" into two halves:

* a **workflow** (a bundled package under ``opentraces.workflow_templates``)
  whose ``scripts/build_rows.py`` projects bucket traces into a typed JSONL row
  stream, run via :func:`opentraces.core.workflow_runner.execute_workflow`;
* a **consumer** (a package under ``opentraces.consumers``) that owns scope
  construction, reads the row stream, and renders or acts on exactly one
  destination (a PR body, an optimized skill, a dashboard, ...).

The contract is deliberately small: workflows never import consumers, and the
only thing every consumer shares is the "ensure the workflow exists, run it,
read the rows" step, captured here as :func:`run_workflow_rows`. The
:class:`WorkflowConsumer` Protocol documents the shape a consumer exposes to its
CLI surface; it is structural, so consumers satisfy it without subclassing.

CLI placement is unchanged by this split: each consumer's user-facing verb stays
where users already expect it (``trail blame pr`` for the PR consumer,
``workflow optimize`` for SkillOpt). ``consumers/`` is an internal implementation
boundary, not a new command group.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..core.workflow_runner import WorkflowExecutionResult, execute_workflow
from ..core.workflows import create_workflow, load_workflow


def ensure_workflow_installed(name: str, *, template: str | None = None) -> None:
    """Materialize a bundled workflow into ``~/.opentraces/workflows`` if absent.

    Idempotent: a second call is a no-op load. ``template`` defaults to ``name``
    (bundled templates are named after the workflow they install).
    """
    try:
        load_workflow(name)
    except (FileNotFoundError, ValueError):
        create_workflow(name, template=template or name, replace=True)


@dataclass(frozen=True)
class WorkflowRows:
    """The row stream a consumer reads, plus its execution provenance."""

    workflow_name: str
    output_path: Path
    rows: list[dict[str, Any]]
    execution: WorkflowExecutionResult

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class ConsumerArtifact:
    """What a consumer renders to its destination.

    ``kind`` names the destination shape (e.g. ``"pr_body"``, ``"skill"``).
    ``path`` / ``text`` carry the rendered artifact; ``metadata`` holds a small
    structured summary suitable for ``--json`` output.
    """

    kind: str
    path: Path | None = None
    text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def run_workflow_rows(
    workflow_name: str,
    *,
    scope: dict[str, Any],
    output_path: Path,
    ensure: bool = True,
    template: str | None = None,
) -> WorkflowRows:
    """Ensure the workflow is installed, run it over ``scope``, parse JSONL rows.

    This is the single shared primitive across consumers; everything else
    (scope construction, rendering, destination side effects) is consumer-owned.
    """
    if ensure:
        ensure_workflow_installed(workflow_name, template=template)
    output_path = Path(output_path)
    execution = execute_workflow(
        workflow_name, scope=scope, output_path=output_path, executor="script"
    )
    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return WorkflowRows(
        workflow_name=workflow_name,
        output_path=output_path,
        rows=rows,
        execution=execution,
    )


@runtime_checkable
class WorkflowConsumer(Protocol):
    """Structural contract a consumer exposes to its CLI surface.

    A consumer reads a workflow's rows and produces one :class:`ConsumerArtifact`.
    Implementations are plain functions or classes; they need only provide these
    attributes/methods to satisfy the Protocol.
    """

    #: stable consumer identifier (e.g. "skill_opt", "branch_pr")
    name: str
    #: the workflow this consumer reads
    workflow_name: str

    def run(self, request: Any) -> ConsumerArtifact:
        """Build scope, run the workflow, render the destination artifact."""
        ...
