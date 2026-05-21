"""Pending plan 083 Codex capture checkpoints.

Each checkpoint is artifact-preferred and inert until its corresponding
``tests/otbox/captures/<scenario>/`` artifact exists. This lets journey
TOMLs pin the correct future lane today while default CI reports SKIP
instead of accidentally exercising assertions against the linear Codex
artifact.
"""

from __future__ import annotations

from ._captured_codex_common import register_codex_capture_checkpoint


register_codex_capture_checkpoint(
    name="c-captured-codex-multi-file-session",
    capture_name="codex-multi-file-patch",
    description=(
        "Artifact-preferred Codex multi-file patch capture checkpoint. "
        "Pending until tests/otbox/captures/codex-multi-file-patch exists."
    ),
)

register_codex_capture_checkpoint(
    name="c-captured-codex-subagent-session",
    capture_name="codex-subagent-edit",
    description=(
        "Artifact-preferred Codex sub-agent capture checkpoint. "
        "Pending until tests/otbox/captures/codex-subagent-edit exists."
    ),
)

register_codex_capture_checkpoint(
    name="c-captured-codex-compacted-session",
    capture_name="codex-context-compaction",
    description=(
        "Artifact-preferred Codex context-compaction capture checkpoint. "
        "Pending until tests/otbox/captures/codex-context-compaction exists."
    ),
)

register_codex_capture_checkpoint(
    name="c-captured-codex-skill-session",
    capture_name="codex-skill-invocation",
    description=(
        "Artifact-preferred Codex skill-invocation capture checkpoint. "
        "Pending until tests/otbox/captures/codex-skill-invocation exists."
    ),
    extra_provides={"skills": ["opentraces"]},
)

register_codex_capture_checkpoint(
    name="c-captured-codex-resume-session",
    capture_name="codex-resume-continue",
    description=(
        "Artifact-preferred Codex resume/fork capture checkpoint. "
        "Pending until tests/otbox/captures/codex-resume-continue exists."
    ),
)

register_codex_capture_checkpoint(
    name="c-captured-codex-mcp-session",
    capture_name="codex-mcp-tool",
    description=(
        "Artifact-preferred Codex MCP capture checkpoint. "
        "Pending until tests/otbox/captures/codex-mcp-tool exists."
    ),
    extra_provides={"mcp_servers_connected": 1},
)

register_codex_capture_checkpoint(
    name="c-captured-codex-security-session",
    capture_name="codex-security-redaction",
    description=(
        "Artifact-preferred Codex security-redaction capture checkpoint. "
        "Pending until tests/otbox/captures/codex-security-redaction exists."
    ),
    extra_provides={"has_security_findings": True},
)

register_codex_capture_checkpoint(
    name="c-mixed-agent-parity-bucket",
    capture_name="mixed-agent-bucket-parity",
    description=(
        "Artifact-preferred mixed Claude/Codex bucket parity checkpoint. "
        "Pending until tests/otbox/captures/mixed-agent-bucket-parity exists."
    ),
    extra_provides={"captured_traces": 2},
)

register_codex_capture_checkpoint(
    name="c-codex-full-parity-latest",
    capture_name="codex-full-parity-latest",
    description=(
        "Artifact-preferred aggregate Codex full-parity checkpoint. "
        "Pending until tests/otbox/captures/codex-full-parity-latest exists."
    ),
    extra_provides={
        "captured_traces": 2,
        "skills": ["opentraces"],
        "mcp_servers_connected": 1,
        "has_security_findings": True,
    },
)
