"""CI lane model (otbox 2.0 phase 3).

Every journey belongs to exactly one CI lane. The lane decides WHERE the
journey executes; the executed-evidence ledger decides what its verdicts are
worth. Lanes replace the 1.0 tier=0/1 opt-in split as the gate input —
``tier`` survives only as the loader's derivation default.

Lanes:

  pr           hermetic, every PR/push: the fixed sentinel set (fast, no
               network, synthetic worlds only)
  nightly      the full matrix: fetched real-agent artifacts, tmux journeys
  local-agents manual ritual on the maintainer machine: real agent binaries,
               real HF/browser auth — NEVER a GitHub lane

Reachability rule (catalogue lint rule ``lane-unreachable``): a journey's
``requires`` must be satisfiable by its lane's capability set — a journey
that can never run in its declared lane is the 1.0 graveyard pattern and
fails collection.

NOTE (design delta, recorded in the run log): the TOML key ``lane`` was
already taken by the plan-063 JTBD bucket lane ("core"), so the CI lane
field is ``ci_lane``.
"""

from __future__ import annotations

CI_LANES = ("pr", "nightly", "local-agents")

# Capability vocabulary comes from journey._capabilities(). These reflect what
# each lane's runner ACTUALLY provides — the ubuntu nightly has tmux but NOT
# terminal-control (termctrl, a cargo-installed real-terminal driver), so
# termctrl-driving journeys belong in the manual local-agents ritual, not CI.
# (Claiming termctrl for nightly made a real-terminal journey SKIP -> graveyard-
# red in the first green-candidate nightly.)
LANE_CAPABILITIES: dict[str, frozenset[str]] = {
    "pr": frozenset({"cli", "git"}),
    "nightly": frozenset({"cli", "git", "tmux", "fetched_artifacts"}),
    "local-agents": frozenset(
        {"cli", "git", "tmux", "termctrl", "tier1", "real_repl", "live_hf",
         "fetched_artifacts"}
    ),
}

# Capabilities that legitimately vary by host or opt-in gate. A tier-0
# journey SKIPping on one of these is a visible pytest skip; SKIPping on
# anything else (cli/git missing = broken box, precondition pin conflicts =
# authoring bugs) is graveyard formation and hard-fails the sweep (issue #52).
# fetched_artifacts is included because journey._capabilities() never emits
# it — it is provided only by the nightly lane's artifact-fetch step.
HOST_CONDITIONAL_CAPS = frozenset(
    {"tmux", "termctrl", "tier1", "real_repl", "live_hf", "fetched_artifacts"}
)


def tier0_skip_disposition(reason: str, missing: set[str]) -> str:
    """'skip' (legitimate host/opt-in gap) or 'fail' (graveyard formation)."""
    if (
        reason.startswith("missing capabilities")
        and missing
        and missing <= HOST_CONDITIONAL_CAPS
    ):
        return "skip"
    return "fail"


# The fixed PR-lane sentinel set: fast, hermetic, one journey per major
# surface (capture chain, bucket, discovery, trail, security, datasets,
# config, health). Editing this list is a reviewed diff by design.
SENTINEL_JOURNEYS = (
    "cli-lifecycle",
    "verify-install",
    "doctor-health",
    "onboard-repo",
    "configure-settings",
    "inspect-security-pipeline",
    "bucket-inspect",
    "trace-map-and-slice",
    "trail-blame-and-graph",
    "security-sanitize-captured-content",
    "agent-session-to-published-dataset",
    "dataset-security-required-rejection",
    "slicer-conformance",
    # CLI v7 core (epic #129) — one real-execution sentinel per v7 surface.
    "setup-roundtrip",
    "status-safety-gate",
    "trace-map-dive",
    "trail-lenses",
    # Seal-family M2 (#180) — the dataset watermark/verify/publish-clearance
    # lifecycle and the capsule mini-bucket redaction floor.
    "dataset-sync-verify-publish",
    "capsule-redaction-floor-on-raw-companions",
    # Seal-family W6 (#214) — ci-pr.yml runs this journey explicitly (see
    # the dedicated "Seal-family W6 sentinel journey" step); listing it here
    # keeps the catalogue's derived ci_lane metadata ("pr") in sync with
    # where it actually runs instead of silently deriving "nightly".
    "replay-contract-capture-completeness",
)


def derive_ci_lane(doc: dict) -> str:
    """Resolve a journey's CI lane: explicit ``ci_lane`` wins, else derive.

    Derivation default: tier 0 journeys run nightly (the full matrix); the
    PR lane is the explicit sentinel subset; tier 1 (the 1.0 opt-in split)
    maps to local-agents until each journey is re-homed deliberately.
    """
    explicit = doc.get("ci_lane")
    if explicit:
        return str(explicit)
    if doc.get("name", "") in SENTINEL_JOURNEYS:
        return "pr"
    return "nightly" if int(doc.get("tier", 0)) == 0 else "local-agents"


def lane_unreachable(ci_lane: str, requires: list[str]) -> list[str]:
    """Return the capabilities in ``requires`` the lane can never provide."""
    caps = LANE_CAPABILITIES.get(ci_lane)
    if caps is None:
        return [f"<unknown lane {ci_lane!r}>"]
    return [r for r in requires if r not in caps]
