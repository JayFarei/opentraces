"""`c-captured-codex-real-session` for plan 083 C1."""

from __future__ import annotations

from ._captured_codex_common import register_codex_capture_checkpoint


register_codex_capture_checkpoint(
    name="c-captured-codex-real-session",
    capture_name="codex-linear-edit",
    description=(
        "Artifact-preferred Codex linear-edit capture checkpoint. "
        "Restores tests/otbox/captures/codex-linear-edit when present; "
        "otherwise records a missing-artifact audit marker without "
        "requiring a real Codex binary in default CI."
    ),
)
