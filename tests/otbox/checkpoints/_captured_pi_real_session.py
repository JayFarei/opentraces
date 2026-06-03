"""Pi linear captured-session checkpoint registration (plan 091)."""

from __future__ import annotations

from ._captured_pi_common import register_pi_capture_checkpoint

register_pi_capture_checkpoint(
    name="c-captured-pi-real-session",
    capture_name="pi-linear-edit",
    description=(
        "Artifact-preferred Pi linear edit capture from tests/otbox/captures/"
        "pi-linear-edit, with deterministic synthetic fallback."
    ),
)
