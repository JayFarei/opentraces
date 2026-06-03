"""Artifact-preferred plan 083 Codex capture checkpoints.

Each checkpoint is inert until its corresponding
``tests/otbox/captures/<scenario>/`` snapshot.tar.gz artifact exists.
This lets journey TOMLs declare the correct capture lane while default
CI reports SKIP instead of accidentally exercising assertions against
the missing artifact.
"""

from __future__ import annotations

from ._captured_codex_common import (
    register_codex_capture_checkpoint,
    register_codex_full_parity_checkpoint,
    register_mixed_agent_bucket_checkpoint,
)


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
    name="c-captured-codex-permission-session",
    capture_name="codex-permission-request",
    description=(
        "Artifact-preferred Codex permission-boundary capture checkpoint. "
        "Pending until tests/otbox/captures/codex-permission-request exists."
    ),
)

register_codex_capture_checkpoint(
    name="c-captured-codex-readonly-session",
    capture_name="codex-readonly-search",
    description=(
        "Artifact-preferred Codex read/search-only capture checkpoint. "
        "Pending until tests/otbox/captures/codex-readonly-search exists."
    ),
)

register_codex_capture_checkpoint(
    name="c-captured-codex-watcher-session",
    capture_name="codex-watcher-backstop",
    description=(
        "Artifact-preferred Codex watcher-backstop capture checkpoint. "
        "Pending until tests/otbox/captures/codex-watcher-backstop exists."
    ),
)

register_codex_capture_checkpoint(
    name="c-captured-codex-subagent-compaction-session",
    capture_name="codex-subagent-compaction",
    description=(
        "Artifact-preferred Codex sub-agent plus compaction capture checkpoint. "
        "Pending until tests/otbox/captures/codex-subagent-compaction exists."
    ),
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

register_mixed_agent_bucket_checkpoint(
    name="c-mixed-agent-parity-bucket",
    codex_capture_name="codex-linear-edit",
    claude_capture_name="add-helper-function",
    description=(
        "Artifact-preferred mixed-agent-bucket-parity checkpoint. "
        "Composes tests/otbox/captures/codex-linear-edit with the frozen "
        "Claude add-helper-function capture when both artifacts exist."
    ),
)

register_codex_full_parity_checkpoint(
    name="c-codex-full-parity-latest",
    representative_capture_name="codex-linear-edit",
    capture_names=[
        "codex-linear-edit",
        "codex-bash-debugging",
        "codex-multi-file-patch",
        "codex-subagent-edit",
        "codex-context-compaction",
        "codex-skill-invocation",
        "codex-resume-continue",
        "codex-mcp-tool",
        "codex-permission-request",
        "codex-readonly-search",
        "codex-watcher-backstop",
        "codex-security-redaction",
        "codex-subagent-compaction",
    ],
    description=(
        "Artifact-preferred codex-full-parity-latest aggregate checkpoint. "
        "Provides the aggregate release gate only when every individual "
        "Codex parity artifact exists; restores codex-linear-edit as the "
        "representative smoke project."
    ),
)
