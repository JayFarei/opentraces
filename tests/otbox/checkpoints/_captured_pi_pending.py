"""Pi parity checkpoint registrations for plan 091."""

from __future__ import annotations

from ._captured_pi_common import (
    register_mixed_agent_pi_bucket_checkpoint,
    register_pi_capture_checkpoint,
    register_pi_full_parity_checkpoint,
)

_PI_CAPTURE_NAMES = [
    "pi-linear-edit",
    "pi-provider-context",
    "pi-compaction",
    "pi-branch-rewind",
    "pi-readonly-search",
    "pi-security-redaction",
]

register_pi_capture_checkpoint(
    name="c-captured-pi-provider-context",
    capture_name="pi-provider-context",
    description="Pi provider/context capture proving live Context Tree layers (synthetic fallback when artifact absent).",
)
register_pi_capture_checkpoint(
    name="c-captured-pi-compacted-session",
    capture_name="pi-compaction",
    description="Pi compaction/session tree capture scaffold (synthetic fallback when artifact absent).",
    extra_provides={"compactions": 1},
)
register_pi_capture_checkpoint(
    name="c-captured-pi-branch-session",
    capture_name="pi-branch-rewind",
    description="Pi branch/rewind session capture scaffold (synthetic fallback when artifact absent).",
)
register_pi_capture_checkpoint(
    name="c-captured-pi-readonly-session",
    capture_name="pi-readonly-search",
    description="Pi readonly OpenTraces-tool session capture scaffold (synthetic fallback when artifact absent).",
)
register_pi_capture_checkpoint(
    name="c-captured-pi-security-session",
    capture_name="pi-security-redaction",
    description="Pi security/redaction capture scaffold (synthetic fallback when artifact absent).",
    extra_provides={"has_security_findings": True},
)

register_mixed_agent_pi_bucket_checkpoint(
    name="c-mixed-agent-pi-bucket",
    pi_capture_name="pi-linear-edit",
    codex_capture_name="codex-linear-edit",
    claude_capture_name="add-helper-function",
    description="Composite Claude/Codex/Pi bucket v2 parity checkpoint.",
)

register_pi_full_parity_checkpoint(
    name="c-pi-full-parity-latest",
    capture_names=_PI_CAPTURE_NAMES,
    representative_capture_name="pi-linear-edit",
    description="Aggregate sentinel over the Pi capture checkpoint family.",
)
