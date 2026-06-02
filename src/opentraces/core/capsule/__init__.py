"""Trace Capsule subsystem (plan 082, v1 share-first tracer bullet).

A *trace capsule* packages one failing agent session into a portable, redacted,
agent-consumable document so a maintainer's agent can re-pose the captured intent
against current code. v1 is SHARE-first: ``export`` -> ``share`` (mint a single
self-contained, SHA-pinned ``capsule.json`` URL) -> ``issue create`` (attach the
URL + the ``capsule open`` command to a GitHub issue). The maintainer's own agent
is the runner; ``capsule open <ref> --json`` is the zero-bespoke-parsing consume
verb that returns the frozen ``opentraces.capsule.v1`` envelope.

See ``kb/plans/082-agent-bug-capsule.md`` (Autoreview Resolution) for the design.
"""

from __future__ import annotations

from .contract import (
    CAPSULE_SCHEMA_VERSION,
    CapsuleSchemaAheadError,
    build_capsule_id,
    freeze_capsule,
    validate_capsule,
)
from .export import CapsuleExportError, export_capsule
from .redaction import (
    REDACTION_FLOOR,
    RedactionGateError,
    assert_redaction_gate,
)
from .render import render_capsule_markdown, render_issue_body
from .share import (
    copy_to_clipboard,
    mint_capsule_url,
    write_capsule_dir,
)

__all__ = [
    "CAPSULE_SCHEMA_VERSION",
    "CapsuleSchemaAheadError",
    "CapsuleExportError",
    "REDACTION_FLOOR",
    "RedactionGateError",
    "assert_redaction_gate",
    "build_capsule_id",
    "copy_to_clipboard",
    "export_capsule",
    "freeze_capsule",
    "mint_capsule_url",
    "render_capsule_markdown",
    "render_issue_body",
    "validate_capsule",
    "write_capsule_dir",
]
