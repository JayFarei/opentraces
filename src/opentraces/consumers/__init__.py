"""Workflow consumers: read a workflow's row stream, render one destination.

See :mod:`opentraces.consumers.contract` for the workflow -> consumer contract
and the shared :func:`run_workflow_rows` primitive. Each subpackage is one
consumer (``skill_opt`` optimizes a skill; ``branch_pr`` renders a PR body).
"""

from .contract import (
    ConsumerArtifact,
    WorkflowConsumer,
    WorkflowRows,
    ensure_workflow_installed,
    run_workflow_rows,
)

__all__ = [
    "ConsumerArtifact",
    "WorkflowConsumer",
    "WorkflowRows",
    "ensure_workflow_installed",
    "run_workflow_rows",
]
