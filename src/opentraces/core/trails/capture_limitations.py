"""Closed vocabulary for Trace Trails capture_limitations.

Phase 5 commits to a closed list so capture gaps stay queryable. Any new tag
must be added here and across all emitters together. New tags must also be
documented in ``kb/plans/054-trace-trails-vcs-anchored-lineage.md`` under the
Phase 5 capture limitations section.

These tags describe what the *capture pipeline* observed during a session.
They are deliberately distinct from the projection-time ``trail_limitations``
and per-observation ``limitations`` reported by ``trail follow``, which
describe what the follow projection could compute from current repo state.
"""
from __future__ import annotations

# Closed Phase 5 vocabulary. Order matters for stable serialization in tests
# that assert on the public list, but emitters should treat the set as
# unordered.
CONCURRENT_WRITER_OVERLAP = "concurrent_writer_overlap"
UNBOUNDED_MUTATION_WINDOW = "unbounded_mutation_window"
BACKGROUND_PROCESS_OVERLAP = "background_process_overlap"
HOOK_ONLY = "hook_only"
HOOK_PAYLOAD_STATE_MISMATCH = "hook_payload_state_mismatch"
SESSION_TERMINATED_UNEXPECTEDLY = "session_terminated_unexpectedly"
WATCHER_BUFFER_OVERFLOW = "watcher_buffer_overflow"

CAPTURE_LIMITATIONS: tuple[str, ...] = (
    CONCURRENT_WRITER_OVERLAP,
    UNBOUNDED_MUTATION_WINDOW,
    BACKGROUND_PROCESS_OVERLAP,
    HOOK_ONLY,
    HOOK_PAYLOAD_STATE_MISMATCH,
    SESSION_TERMINATED_UNEXPECTEDLY,
    WATCHER_BUFFER_OVERFLOW,
)


def is_known_capture_limitation(tag: str) -> bool:
    return tag in CAPTURE_LIMITATIONS


def assert_known_capture_limitations(tags: list[str]) -> None:
    """Raise ``ValueError`` if any tag is outside the closed vocabulary.

    Emitters should call this before appending events so unknown tags fail
    fast at the source rather than leaking into the canonical event log.
    """
    unknown = [tag for tag in tags if tag not in CAPTURE_LIMITATIONS]
    if unknown:
        raise ValueError(
            f"unknown capture_limitations: {unknown}; closed vocabulary is "
            f"{list(CAPTURE_LIMITATIONS)}"
        )
