"""`c-captured-codex-bash-session` for plan 083 C2/C12."""

from __future__ import annotations

from ._captured_codex_common import register_codex_capture_checkpoint


register_codex_capture_checkpoint(
    name="c-captured-codex-bash-session",
    capture_name="codex-bash-debugging",
    description=(
        "Artifact-preferred Codex bash-debugging capture checkpoint. "
        "Restores tests/otbox/captures/codex-bash-debugging when present; "
        "otherwise records a missing-artifact audit marker without "
        "requiring a real Codex binary in default CI."
    ),
)
