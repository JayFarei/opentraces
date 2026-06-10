"""Catalogue lint — collection-time strictness for journey TOMLs (otbox 2.0 phase 2).

Principle: invalid is unrepresentable, not detectable. A journey that names an
unknown assertion kind, an unregistered checkpoint, an unknown precondition
key, an undeclared step ref, or a self-comparing equals_var must fail the
suite at lint time — never surface as a runtime surprise inside a journey
that may not even run in the current lane.

Rules (rule ids are stable; QUARANTINE.toml entries reference them):

  unknown-assertion-kind   [[assertions]].kind not in journey.ASSERTION_KINDS
  unregistered-checkpoint  from_checkpoints entry not in checkpoints.REGISTRY
  unknown-precondition     [preconditions] key outside PRECONDITION_VOCAB
  unknown-step-ref         assertion step= names no declared [[steps]].id
  self-comparing-var       equals_var resolves to the SAME step:path it asserts on
  lane-unreachable         requires names a capability the journey's CI lane
                           can never provide (the 1.0 graveyard pattern)

Quarantine: tests/otbox/catalogue/QUARANTINE.toml downgrades matching
violations to warnings until `expires`. Expired entries, entries naming
unknown journeys, and a quarantine larger than MAX_QUARANTINE_ENTRIES are
themselves violations — the valve cannot silently widen.
"""

from __future__ import annotations

import datetime as _dt
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .journey import ASSERTION_KINDS, CATALOGUE_DIR

# The full key set understood by journey._checkpoint_satisfies. Adding a key
# there REQUIRES adding it here (the sentinel test cross-checks the source).
PRECONDITION_VOCAB = frozenset(
    {
        "min_captured_traces",
        "requires_survival_states",
        "requires_skills",
        "requires_branch_commits_min",
        "requires_security_findings",
        "context_tree_built",
        "otel_captures_present",
        "otel_settings_patched",
        "otlp_receiver_running",
        "otel_bypass_active",
        "mcp_servers_connected",
        "legacy_world_restored",
        "migration_applied",
        "no_data_loss",
        "migration_idempotent",
        "pre_migration_schema",
        # Issue #42 — bucket-spine-v2 family (plan 080 phases B/C/D).
        "bucket_spine_v2_layout",
        "pushed_to_fake_remote",
        "orphan_blob_injected",
        "dangling_ref_injected",
        "bucket_only_no_git_ref",
        "local_blobs_dropped",
        "lazy_projection_enabled",
        "events_mirror_v1_populated",
        # Issue #42 — context-tree family (plan 077 phase 4).
        "requires_branch_types",
        "requires_compactions_min",
        "requires_messages_layer_bytes_min",
        "requires_edit_commits_min",
        # Issue #42 — trail-mature precondition (entry 4).
        "git_repo_present",
        "post_commit_hook_installed",
    }
)

MAX_QUARANTINE_ENTRIES = 15
QUARANTINE_PATH = Path(__file__).parent / "catalogue" / "QUARANTINE.toml"


@dataclass
class LintViolation:
    journey: str
    rule: str
    message: str
    quarantined: bool = False

    def __str__(self) -> str:  # pragma: no cover - display helper
        flag = " [quarantined]" if self.quarantined else ""
        return f"{self.journey}: {self.rule}: {self.message}{flag}"


@dataclass
class QuarantineEntry:
    """One tracked debt DECISION: a family of journeys sharing one reason,
    one issue URL, and one expiry. The MAX_QUARANTINE_ENTRIES cap counts
    decisions, not journeys — grouping is allowed, silent widening is not."""

    journeys: list[str]
    rules: list[str]
    reason: str
    issue: str
    added: _dt.date
    expires: _dt.date


def load_quarantine(path: Path = QUARANTINE_PATH) -> list[QuarantineEntry]:
    if not path.exists():
        return []
    doc = tomllib.loads(path.read_text())
    out = []
    for raw in doc.get("entries", []):
        out.append(
            QuarantineEntry(
                journeys=[str(j) for j in raw["journeys"]],
                rules=[str(r) for r in raw.get("rules", ["*"])],
                reason=str(raw["reason"]),
                issue=str(raw["issue"]),
                added=raw["added"],
                expires=raw["expires"],
            )
        )
    return out


def _quarantine_match(
    entries: list[QuarantineEntry], journey: str, rule: str, today: _dt.date
) -> bool:
    for e in entries:
        if journey in e.journeys and e.expires >= today and ("*" in e.rules or rule in e.rules):
            return True
    return False


def _lint_one(path: Path) -> list[LintViolation]:
    from .checkpoints import REGISTRY  # late import: registry imports journey

    doc = tomllib.loads(path.read_text())
    name = doc.get("name", path.stem)
    violations: list[LintViolation] = []

    step_ids = {s.get("id") for s in doc.get("steps", []) if s.get("id")}

    for cp in doc.get("from_checkpoints", []):
        if cp not in REGISTRY:
            violations.append(
                LintViolation(name, "unregistered-checkpoint", f"{cp!r} is not registered")
            )

    from .lanes import derive_ci_lane, lane_unreachable

    ci_lane = derive_ci_lane(doc)
    unreachable = lane_unreachable(ci_lane, [str(r) for r in doc.get("requires", [])])
    if unreachable:
        violations.append(
            LintViolation(
                name,
                "lane-unreachable",
                f"lane {ci_lane!r} can never provide {unreachable!r}",
            )
        )

    for key in (doc.get("preconditions") or {}):
        if key not in PRECONDITION_VOCAB:
            violations.append(
                LintViolation(
                    name,
                    "unknown-precondition",
                    f"{key!r} is not in PRECONDITION_VOCAB (the resolver silently ignores it)",
                )
            )

    for i, spec in enumerate(doc.get("assertions", [])):
        kind = spec.get("kind", "")
        if kind not in ASSERTION_KINDS:
            violations.append(
                LintViolation(
                    name, "unknown-assertion-kind", f"assertions[{i}].kind={kind!r}"
                )
            )
        ref = spec.get("step")
        if ref is not None and ref not in step_ids:
            violations.append(
                LintViolation(
                    name,
                    "unknown-step-ref",
                    f"assertions[{i}].step={ref!r} names no declared step id",
                )
            )
        ev = spec.get("equals_var")
        if isinstance(ev, str) and ":" in ev:
            var_step, _, var_path = ev.partition(":")
            if var_step == spec.get("step") and var_path == spec.get("path"):
                violations.append(
                    LintViolation(
                        name,
                        "self-comparing-var",
                        f"assertions[{i}] compares {ev!r} against itself",
                    )
                )

    return violations


def lint_catalogue(
    catalogue_dir: Path | None = None,
    quarantine_path: Path | None = None,
    today: _dt.date | None = None,
) -> tuple[list[LintViolation], list[LintViolation]]:
    """Return ``(failing, quarantined)`` violations for the whole catalogue.

    ``failing`` includes quarantine-hygiene violations (expired entries,
    unknown journeys, oversized quarantine) — the valve cannot widen quietly.
    """
    cat_dir = catalogue_dir or CATALOGUE_DIR
    today = today or _dt.date.today()
    entries = load_quarantine(quarantine_path or QUARANTINE_PATH)

    known_journeys = set()
    failing: list[LintViolation] = []
    quarantined: list[LintViolation] = []

    for path in sorted(cat_dir.glob("*.toml")):
        doc_name = tomllib.loads(path.read_text()).get("name", path.stem)
        known_journeys.add(doc_name)
        for v in _lint_one(path):
            if _quarantine_match(entries, v.journey, v.rule, today):
                v.quarantined = True
                quarantined.append(v)
            else:
                failing.append(v)

    # Quarantine hygiene — these are never themselves quarantinable.
    if len(entries) > MAX_QUARANTINE_ENTRIES:
        failing.append(
            LintViolation(
                "<quarantine>",
                "quarantine-overflow",
                f"{len(entries)} entries > max {MAX_QUARANTINE_ENTRIES}",
            )
        )
    for e in entries:
        label = e.journeys[0] if e.journeys else "<empty>"
        if e.expires < today:
            failing.append(
                LintViolation(
                    label, "quarantine-expired", f"expired {e.expires} ({e.reason})"
                )
            )
        for j in e.journeys:
            if j not in known_journeys:
                failing.append(
                    LintViolation(
                        j, "quarantine-unknown-journey", "entry names no catalogue journey"
                    )
                )
        if not e.issue.startswith("http"):
            failing.append(
                LintViolation(label, "quarantine-no-issue", f"issue={e.issue!r}")
            )
    return failing, quarantined
