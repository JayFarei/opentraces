"""Journey-contract map: spec journeys J1-J18 -> otbox verifier journeys.

The spec skeleton (kb/projects/otbox-spec-skeleton.md) defines 18
user journeys. This map binds each to the catalogue journeys that verify it;
the claims ledger derives each spec journey's STATUS from the executed-
evidence run ledger (never from this file — the map only says what counts
as evidence, the ledger says whether it ran and passed).

A spec journey's status is the WEAKEST of its mapped journeys' states:
verified (all executed PASS) > partial (some) > pending (none) — with
quarantined rows reported alongside, never hidden.
"""

from __future__ import annotations

SPEC_JOURNEYS: dict[str, dict] = {
    "J1-enroll-machine": {
        "title": "Enroll a machine (install -> setup -> auth -> doctor green)",
        "journeys": ["verify-install", "onboard-integrations", "connect-hf-identity",
                     "installed-cli-doctor"],
    },
    "J2-enroll-project": {
        "title": "Enroll/exclude a project (init / tracking-mode / backfill)",
        "journeys": ["onboard-repo", "attribution-backfill"],
    },
    "J3-maintain-bucket": {
        "title": "Maintain the bucket (status/verify/repair/prune, remote sync)",
        "journeys": ["bucket-inspect", "bucket-remote-push", "bucket-remote-pull",
                     "bucket-remote-digests"],
    },
    "J4-upgrade": {
        "title": "Upgrade across a version boundary, read paths intact",
        "journeys": ["migration-s12-end-to-end-upgrade", "maintain-install"],
    },
    "J5-capture-session": {
        "title": "Capture a session per harness (claude / codex / pi)",
        "journeys": ["agent-session-trail-explain-happy", "codex-parity-linear",
                     "pi-extension-capture-linear"],
    },
    "J6-find-past-work": {
        "title": "Find past work (query -> map -> slice -> get)",
        "journeys": ["trace-map-and-slice", "build-dataset-lineage-search"],
    },
    "J7-explain-commit-pr": {
        "title": "Explain a commit/PR (blame commit / blame pr render)",
        "journeys": ["trail-blame-and-graph", "pr-blame-on-captured-branch"],
    },
    "J8-track-survival": {
        "title": "Track what survived (a real revert shows reverted)",
        "journeys": ["survival-walk", "survival-walk-reverted"],
    },
    "J9-context-resume": {
        "title": "Inspect/resume context (ctx tree/step -> resume)",
        "journeys": ["context-tree-demo-acceptance", "context-tree-otel-capture-fidelity"],
    },
    "J10-publish-dataset": {
        "title": "Build & publish a dataset (workflow -> review -> publish)",
        "journeys": ["agent-session-to-published-dataset", "cli-publish-happy-path",
                     "dataset-sync-skill-history"],
    },
    "J11-secure-egress": {
        "title": "Secure before egress (a planted secret cannot leave)",
        "journeys": ["security-sanitize-captured-content",
                     "dataset-security-required-rejection"],
    },
    "J12-capsule": {
        "title": "Share a capsule (export -> preview -> share/issue -> verdict)",
        "journeys": ["capsule-command-smoke", "capsule-dependency-unblock"],
    },
    "J13-agent-drives-cli": {
        "title": "Agent drives the CLI (setup prompt, --json, next_steps)",
        "journeys": ["agent-protocol-orient", "prompt-install-auth-flow"],
    },
    "J14-mine-skill-intel": {
        "title": "Mine skill intelligence (episodes/rollouts/eval-tasks)",
        "journeys": ["dataset-sync-skill-history", "skill-verifier-factory-echo"],
    },
    "J15-calibrate-verifier": {
        "title": "Build & calibrate a verifier (honest trust-ladder verdicts)",
        "journeys": ["skill-verifier-command-smoke", "skill-verifier-factory-echo"],
    },
    "J16-optimize-with-rubric": {
        "title": "Optimize against a rubric (reward provenance)",
        "journeys": ["skillopt-online-loop-echo"],
    },
    "J17-diagnose-recover": {
        "title": "Diagnose & recover (doctor -> remedy -> healthy)",
        "journeys": ["doctor-health", "dev-quality-maintenance"],
    },
    "J18-consume-downstream": {
        "title": "Consume as a downstream (remote reads, row -> evidence)",
        "journeys": ["recreate-trace-environment", "live-hf-read-remote"],
    },
}


def spec_status(states: list[str]) -> str:
    """verified > partial > pending from the mapped journeys' verdicts."""
    if states and all(s == "PASS" for s in states):
        return "verified"
    if any(s == "PASS" for s in states):
        return "partial"
    return "pending"
