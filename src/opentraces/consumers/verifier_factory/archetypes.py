"""Verifier archetype registry for the Skill Verifier Factory.

An *archetype* is a reusable template for "what does it mean for THIS skill to have
caused better downstream work". Each archetype declares:

* a set of **contract elements** , the trace-grounded things a good skill outcome
  exhibits (e.g. for goal-forge: an outcome statement, a verification surface, a
  blocked-stop condition). Each element carries a **declarative detector**
  (:class:`detectors.DetectorSpec`) — pure data, no code — so the factory can read
  the element off real episodes (the **label-free** signal). The detector is a
  disjunction: the element is present when ANY populated predicate matches.
* the **rule markers** the deterministic CI verifier enforces, one per contract
  element. These ride the proven SkillOpt marker mechanism
  (:func:`consumers.skill_opt.proposers.default_proposer` adds ``rule[<marker>]``;
  :class:`consumers.skill_opt.rerollout.FakeReRolloutRunner` scores by coverage),
  so the gate stays deterministic and network-free.
* **creator questions** , the verifier-creator interview. Each question ships an
  inferred default (label-free) that a human may override (the **labeled** path).
* **live legs** , richer semantic checks (real agent re-rollout, LLM rubric) that
  are declared in the package but default-OFF / opt-in, never run in default CI.

Archetypes are fully **serializable**: :func:`archetype_to_dict` /
:func:`archetype_from_dict` round-trip an archetype (and its declarative detectors)
to YAML/JSON and back, with no ``eval`` and no arbitrary callable. This is what lets
an agent author a contextualized verifier as a spec; the engine interprets it. The
serializer's validator hard-rejects unknown keys, so a hand-authored spec can never
smuggle a ``reward`` / ``gate`` / ``split`` field past the engine (br/56 trust
boundary).

Four curated archetypes ship (goal-forge, tdd, review, docs-update) plus one generic,
skill-agnostic archetype for any skill without a curated one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .detectors import (
    DetectorSpec,
    DetectorSpecError,
    classify_command,
    detector_from_dict,
    detector_matches,
)


class ArchetypeSpecError(ValueError):
    """Raised when an archetype spec dict is structurally invalid (trust boundary)."""


@dataclass(frozen=True)
class ContractElement:
    """One trace-grounded element of a skill's success contract.

    ``marker`` is the SkillOpt rule tag the deterministic verifier enforces.
    ``detectors`` is a declarative, data-only :class:`DetectorSpec` that returns
    True when the element is present in the captured trace.
    """

    element_id: str
    label: str
    marker: str
    detectors: DetectorSpec
    weight: float = 1.0

    def detect(self, evidence: Any) -> bool:
        """Interpret this element's declarative detector against episode evidence."""
        return detector_matches(self.detectors, evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "label": self.label,
            "marker": self.marker,
            "weight": self.weight,
            "detectors": self.detectors.to_dict(),
        }


@dataclass(frozen=True)
class CreatorQuestion:
    """A verifier-creator interview question with an evidence-inferred default."""

    key: str
    question: str
    inferred_default: str
    rationale: str
    options: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "question": self.question,
            "inferred_default": self.inferred_default,
            "rationale": self.rationale,
            "options": list(self.options),
        }


@dataclass(frozen=True)
class LiveLeg:
    """A declared-but-deferred opt-in verifier leg (never runs in default CI)."""

    leg_id: str
    kind: str  # "agent_rerollout" | "llm_rubric"
    description: str
    requires: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "leg_id": self.leg_id,
            "kind": self.kind,
            "description": self.description,
            "requires": list(self.requires),
        }


@dataclass(frozen=True)
class EpisodeEvidence:
    """Normalized, archetype-agnostic view of one mined episode + its TraceRecord.

    Built by :func:`evidence_from_episode`. When the underlying ``TraceRecord`` is
    supplied the evidence is enriched with the real shell commands the agent ran
    (parsed through Codex's ``exec_command`` opacity) and the commit signal, so
    detectors work on Codex traces, not just on Claude's named tools.
    """

    text: str  # lower-cased intent + summary
    tools: tuple[str, ...]
    files: tuple[str, ...]
    outcome_label: str
    outcome_reward: float
    commands: tuple[str, ...] = ()
    command_families: frozenset[str] = frozenset()
    committed: bool = False
    step_count: int = 0

    def has_any(self, *needles: str) -> bool:
        return any(n in self.text for n in needles)

    def any_tool(self, *needles: str) -> bool:
        return any(any(n in t.lower() for n in needles) for t in self.tools)

    def any_file(self, *needles: str) -> bool:
        return any(any(n in f.lower() for n in needles) for f in self.files)

    def has_family(self, *families: str) -> bool:
        return any(f in self.command_families for f in families)

    def any_command(self, *needles: str) -> bool:
        return any(any(n in c.lower() for n in needles) for c in self.commands)


@dataclass(frozen=True)
class VerifierArchetype:
    archetype_id: str
    skill: str
    title: str
    description: str
    semantic_target: str
    contract_elements: tuple[ContractElement, ...]
    creator_questions: tuple[CreatorQuestion, ...]
    live_legs: tuple[LiveLeg, ...]
    baseline_skill: str
    #: minimum distinct source traces for a leakage-safe 3-way split to be credible
    min_traces_for_split: int = 6

    @property
    def markers(self) -> tuple[str, ...]:
        return tuple(el.marker for el in self.contract_elements)

    def to_dict(self) -> dict[str, Any]:
        return archetype_to_dict(self)


def _commands_from_record(record: Any) -> list[str]:
    """Extract the real shell commands an agent ran (Codex exec_command opacity).

    Reads ``record.metadata.normalized_tool_calls[].shell.command`` (canonical,
    Codex + Claude) and falls back to per-step tool-call inputs (``cmd`` / ``command``
    / ``script``). Returns a bounded, de-duplicated, ordered list.
    """
    commands: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            s = value.strip()
            if s not in seen:
                seen.add(s)
                commands.append(s)

    metadata = getattr(record, "metadata", None) or {}
    for ntc in metadata.get("normalized_tool_calls") or ():
        shell = (ntc or {}).get("shell") if isinstance(ntc, dict) else None
        if isinstance(shell, dict):
            _add(shell.get("command") or shell.get("cmd") or shell.get("script"))
    if not commands:
        for step in getattr(record, "steps", None) or ():
            for call in getattr(step, "tool_calls", None) or ():
                inp = getattr(call, "input", None)
                if isinstance(inp, dict):
                    _add(inp.get("cmd") or inp.get("command") or inp.get("script"))
    return commands[:200]


def evidence_from_episode(episode: dict[str, Any], record: Any = None) -> EpisodeEvidence:
    """Project a ``skill-episodes-v1`` row (+ optional TraceRecord) into evidence.

    Without a record the evidence is the lossy projection (text + tool names +
    file list) — backward compatible. With a record it is enriched with the real
    shell commands and the commit signal, and the command families are derived from
    both the commands and the tool-name list so Codex and Claude traces are covered.
    """
    text = " ".join(
        str(episode.get(k) or "") for k in ("user_intent", "task_summary")
    ).lower()

    def _load_list(key: str) -> tuple[str, ...]:
        raw = episode.get(key)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = []
        else:
            parsed = raw or []
        return tuple(str(x) for x in parsed) if isinstance(parsed, list) else ()

    tools = _load_list("tools_used_json")
    commands = tuple(_commands_from_record(record)) if record is not None else ()

    families: set[str] = set()
    for command in commands:
        families |= classify_command(command)
    # Tool names also carry signal (Claude's named tools + the synthetic tool list
    # on injected episodes), so classify them too.
    for tool in tools:
        families |= classify_command(tool)

    committed = False
    step_count = 0
    if record is not None:
        outcome = getattr(record, "outcome", None)
        committed = bool(getattr(outcome, "committed", False))
        step_count = len(getattr(record, "steps", None) or ())

    return EpisodeEvidence(
        text=text,
        tools=tools,
        files=_load_list("files_touched_json"),
        outcome_label=str(episode.get("outcome_label") or "unknown"),
        outcome_reward=float(episode.get("outcome_reward") or 0.0),
        commands=commands,
        command_families=frozenset(families),
        committed=committed,
        step_count=step_count,
    )


# ---------------------------------------------------------------------------
# Archetype definitions (declarative detectors, no code)
# ---------------------------------------------------------------------------
#
# Each ContractElement's detector is a DetectorSpec (pure data). Detection is a
# disjunction over the populated predicate fields, which exactly reproduces the OR
# structure every former closure detector had — the migration is lossless. The
# downstream/landed predicate (committed OR outcome_reward>=0.5 OR label=success) is
# shared verbatim by goal-forge, tdd, and review's "downstream" element.

_DOWNSTREAM_DETECTOR = DetectorSpec(
    committed=True, outcome_reward_min=0.5, outcome_label_in=("success",)
)


_GOAL_FORGE = VerifierArchetype(
    archetype_id="goal_contract_downstream_outcome_v1",
    skill="goal-forge",
    title="Goal contract + downstream outcome",
    description=(
        "A goal-forge invocation is good when the goal it produces is a usable "
        "contract (outcome, verification surface, constraints, honest stop) AND the "
        "downstream agent that ran against that goal actually landed verified work."
    ),
    semantic_target=(
        "messy request -> generated goal -> downstream agent rollout: did the agent "
        "preserve constraints, verify correctly, avoid drift, and stop honestly?"
    ),
    contract_elements=(
        ContractElement(
            "outcome", "Explicit outcome / definition of done",
            "rl.goal-forge.outcome_contract",
            DetectorSpec(text_any=("outcome", "goal", "done means", "definition of done", "finish line")),
        ),
        ContractElement(
            "verification", "Verification surface",
            "rl.goal-forge.verification_surface",
            DetectorSpec(
                command_families=("verification",),
                text_any=("verif", "verified by", "test", "check", "assert", "pass"),
            ),
        ),
        ContractElement(
            "constraints", "Constraint / boundary preservation",
            "rl.goal-forge.constraint_preservation",
            DetectorSpec(text_any=("constraint", "must not", "preserve", "do not", "boundary", "scope")),
        ),
        ContractElement(
            "honest_stop", "Blocked / honest-stop condition",
            "rl.goal-forge.honest_stop",
            DetectorSpec(text_any=("blocked", "stop", "halt", "ask for", "give up", "escalat")),
        ),
        ContractElement(
            "downstream", "Downstream landed/verified outcome",
            "rl.goal-forge.downstream_outcome", _DOWNSTREAM_DETECTOR, weight=1.5,
        ),
    ),
    creator_questions=(
        CreatorQuestion(
            "success_definition",
            "What counts as a successful goal-forge outcome?",
            "Goal carries outcome+verification+constraints+honest-stop AND downstream work landed/verified.",
            "Briefs br/66 (verifier gap) + br/51 (outcome factor vector + firmness).",
            options=("downstream_landed", "contract_complete", "both"),
        ),
        CreatorQuestion(
            "downstream_signal",
            "Which downstream signal is the ground-truth reward?",
            "Trace outcome_reward (committed + verified + Trail survival).",
            "br/56: git-survival reward sits outside the agent trust domain.",
            options=("outcome_reward", "tests_pass", "human_label"),
        ),
        CreatorQuestion(
            "drift_policy",
            "Should constraint drift be penalized even if work landed?",
            "Yes — missing constraint_preservation is a deficient marker regardless of landing.",
            "Goal-forge's value is constraint fidelity, not just completion.",
            options=("penalize", "ignore"),
        ),
    ),
    live_legs=(
        LiveLeg(
            "downstream_agent_rerollout",
            "agent_rerollout",
            "Re-roll a downstream agent against the generated goal and score whether it "
            "preserved constraints / verified / stopped honestly. Opt-in, real agent.",
            requires=("real_agent", "OT_REAL_REPL"),
        ),
        LiveLeg(
            "goal_contract_llm_rubric",
            "llm_rubric",
            "LLM-graded rubric over the produced goal's contract completeness. Opt-in.",
            requires=("llm_endpoint",),
        ),
    ),
    baseline_skill=(
        "# goal-forge (baseline)\n\n"
        "Turn a messy request into a goal.\n"
    ),
)

_TDD = VerifierArchetype(
    archetype_id="tdd_red_green_refactor_v1",
    skill="tdd",
    title="TDD red-green-refactor trace",
    description=(
        "A tdd invocation is good when the trace shows the red-green-refactor cycle: a "
        "failing test authored first, an implementation that makes it pass, and a "
        "refactor pass, with the change landing verified."
    ),
    semantic_target=(
        "red (failing test first) -> green (make it pass) -> refactor, with the test "
        "as the executable intent and a landed verified outcome."
    ),
    contract_elements=(
        ContractElement(
            "red_first", "Failing test authored first",
            "rl.tdd.red_first",
            DetectorSpec(
                file_globs=("test",),
                command_any=("test_", "_test", "tests/"),
                text_any=("failing test", "write a test", "red", "test first"),
            ),
            weight=1.5,
        ),
        ContractElement(
            "green_pass", "Implementation makes tests pass",
            "rl.tdd.green_pass",
            DetectorSpec(
                command_families=("verification",),
                text_any=("make it pass", "green", "passing", "tests pass"),
            ),
        ),
        ContractElement(
            "refactor", "Refactor step",
            "rl.tdd.refactor",
            DetectorSpec(text_any=("refactor", "clean up", "tidy", "simplif", "extract")),
        ),
        ContractElement(
            "downstream", "Landed / verified outcome",
            "rl.tdd.downstream_outcome", _DOWNSTREAM_DETECTOR,
        ),
    ),
    creator_questions=(
        CreatorQuestion(
            "cycle_strictness",
            "Must red precede green for credit?",
            "Yes — red_first is the highest-weight element; test-before-impl ordering is the signal.",
            "br/51 intent hierarchy: tests are the strongest executable intent.",
            options=("strict_order", "presence_only"),
        ),
        CreatorQuestion(
            "refactor_required",
            "Is the refactor step required for full reward?",
            "Partial credit without it; full credit needs all three phases.",
            "Refactor is the lowest-weight element; presence is a bonus.",
            options=("required", "bonus"),
        ),
    ),
    live_legs=(
        LiveLeg(
            "tdd_agent_rerollout",
            "agent_rerollout",
            "Re-roll an agent on the task and assert a test was added and fails before "
            "the fix, then passes after. Opt-in, real agent.",
            requires=("real_agent", "OT_REAL_REPL"),
        ),
    ),
    baseline_skill="# tdd (baseline)\n\nWrite code with tests.\n",
)

_REVIEW = VerifierArchetype(
    archetype_id="review_grounded_findings_v1",
    skill="review",
    title="Review with grounded findings",
    description=(
        "A review invocation is good when its findings are grounded in real artifacts: "
        "each finding cites a file (and ideally a line) present in the trace, rather "
        "than hallucinated, and the review's verdict matches the landed outcome."
    ),
    semantic_target=(
        "findings grounded in the actual diff/code (file:line citations to real "
        "artifacts), not hallucinated, with a verdict consistent with the outcome."
    ),
    contract_elements=(
        ContractElement(
            "grounded", "Findings reference real touched files",
            "rl.review.grounded_findings",
            DetectorSpec(
                has_files=True,
                command_families=("search_read",),
                text_any=("file", "diff", "line", "hunk"),
            ),
            weight=1.5,
        ),
        ContractElement(
            "cite", "Findings cite file:line evidence",
            "rl.review.cite_file_line",
            DetectorSpec(
                command_any=(".py:", ".ts:", ".md:", "line "),
                text_any=("line", "evidence", "cite", "grounded", "reference"),
            ),
        ),
        ContractElement(
            "downstream", "Verdict consistent with landed outcome",
            "rl.review.downstream_outcome", _DOWNSTREAM_DETECTOR,
        ),
    ),
    creator_questions=(
        CreatorQuestion(
            "grounding_floor",
            "What is the minimum grounding for a finding to count?",
            "Each finding must reference a file present in files_touched.",
            "br/56: reward must resist hallucination; ground in real artifacts.",
            options=("file_ref", "file_line_ref", "any"),
        ),
        CreatorQuestion(
            "verdict_consistency",
            "Should the verdict be checked against the landed outcome?",
            "Yes — a 'looks good' verdict on reverted work is a deficient marker.",
            "Trail survival is the tamper-resistant downstream check.",
            options=("check", "ignore"),
        ),
    ),
    live_legs=(
        LiveLeg(
            "review_groundedness_llm_rubric",
            "llm_rubric",
            "LLM-graded rubric scoring each finding's groundedness against the diff. Opt-in.",
            requires=("llm_endpoint",),
        ),
    ),
    baseline_skill="# review (baseline)\n\nReview the change.\n",
)


_DOCS_UPDATE = VerifierArchetype(
    archetype_id="docs_update_reflects_change_v1",
    skill="docs-update",
    title="Docs update reflects the change",
    description=(
        "A docs-update invocation is good when the documentation it edits actually "
        "reflects the code change: it is grounded in the changed code, touches the "
        "right doc artifacts (README / CHANGELOG / docs), records the change, and "
        "removes stale references, with the edit landing."
    ),
    semantic_target=(
        "code change -> docs edit: do the docs reference the real changed code, update "
        "the changelog/readme, and remove stale references (not drift out of date)?"
    ),
    contract_elements=(
        ContractElement(
            "code_grounded", "Docs grounded in the changed code",
            "rl.docs-update.code_grounded",
            DetectorSpec(
                has_files=True,
                command_families=("search_read",),
                text_any=("diff", "code", "file", "function"),
            ),
            weight=1.5,
        ),
        ContractElement(
            "doc_artifact", "Touched the right doc artifact",
            "rl.docs-update.doc_artifact",
            DetectorSpec(
                file_globs=("readme", "changelog", ".md", "docs/", ".rst", ".mdx"),
                command_any=(".md", "readme", "changelog", "docs/"),
                text_any=("readme", "changelog", "docs", "documentation"),
            ),
        ),
        ContractElement(
            "changelog", "Recorded the change (changelog / release notes)",
            "rl.docs-update.changelog",
            DetectorSpec(text_any=("changelog", "release note", "version bump", "what's new", "whats new")),
        ),
        ContractElement(
            "no_stale", "Removed stale / outdated references",
            "rl.docs-update.no_stale",
            DetectorSpec(text_any=("stale", "outdated", "out of date", "no longer", "deprecat", "remove", "obsolete")),
            weight=1.5,
        ),
        ContractElement(
            "downstream", "Landed / verified outcome",
            "rl.docs-update.downstream_outcome", _DOWNSTREAM_DETECTOR,
        ),
    ),
    creator_questions=(
        CreatorQuestion(
            "grounding_floor",
            "Must a docs edit cite the specific changed code to count?",
            "Yes — code_grounded is high-weight; ungrounded docs edits are a failure mode.",
            "br/56: ground docs claims in the real diff to resist drift/hallucination.",
            options=("code_ref", "any_doc_edit"),
        ),
        CreatorQuestion(
            "stale_policy",
            "Is removing stale references required for full reward?",
            "Yes — no_stale is high-weight; leaving outdated docs is the common failure.",
            "Stale docs are the dominant docs-update failure mode in practice.",
            options=("required", "bonus"),
        ),
    ),
    live_legs=(
        LiveLeg(
            "docs_freshness_llm_rubric",
            "llm_rubric",
            "LLM-graded rubric scoring whether the docs edit matches the diff and leaves "
            "no stale references. Opt-in.",
            requires=("llm_endpoint",),
        ),
    ),
    baseline_skill="# docs-update (baseline)\n\nUpdate the docs.\n",
)


ARCHETYPES: dict[str, VerifierArchetype] = {
    a.archetype_id: a for a in (_GOAL_FORGE, _TDD, _REVIEW, _DOCS_UPDATE)
}

ARCHETYPES_BY_SKILL: dict[str, tuple[VerifierArchetype, ...]] = {}
for _arch in ARCHETYPES.values():
    ARCHETYPES_BY_SKILL.setdefault(_arch.skill, ())
    ARCHETYPES_BY_SKILL[_arch.skill] = ARCHETYPES_BY_SKILL[_arch.skill] + (_arch,)


#: The skill-agnostic archetype id. Bound to a concrete skill at resolution time so
#: its markers are skill-namespaced (``rl.<skill>.generic.<element>``). This is what
#: makes the factory generic: ANY skill with trace evidence gets a verifier candidate,
#: not only the curated skills.
GENERIC_ARCHETYPE_ID = "generic_skill_outcome_v1"


def _generic_contract_elements(skill: str) -> tuple[ContractElement, ...]:
    return (
        ContractElement(
            "context_read", "Read / inspected context before acting",
            f"rl.{skill}.generic.context_read",
            DetectorSpec(
                command_families=("search_read",),
                tool_any=("read", "grep", "glob", "exec"),
                has_commands=True,
            ),
        ),
        ContractElement(
            "produced_changes", "Produced concrete changes (files / patches / commit)",
            f"rl.{skill}.generic.produced_changes",
            DetectorSpec(has_files=True, command_families=("edit_write", "git_commit")),
        ),
        ContractElement(
            "verification_run", "Ran a verification command (tests / lint / build)",
            f"rl.{skill}.generic.verification_run",
            DetectorSpec(command_families=("verification",)), weight=1.5,
        ),
        ContractElement(
            "landed_outcome", "Landed / verified downstream outcome",
            f"rl.{skill}.generic.landed_outcome",
            DetectorSpec(
                committed=True,
                command_families=("git_commit",),
                outcome_reward_min=0.5,
                outcome_label_in=("success",),
            ),
            weight=1.5,
        ),
        ContractElement(
            "honest_completion", "Terminal, non-abandoned outcome",
            f"rl.{skill}.generic.honest_completion",
            DetectorSpec(outcome_label_in=("success", "failure")),
        ),
    )


def generic_archetype_for(skill: str) -> VerifierArchetype:
    """Build the skill-agnostic archetype bound to ``skill`` (markers namespaced)."""
    return VerifierArchetype(
        archetype_id=GENERIC_ARCHETYPE_ID,
        skill=skill,
        title=f"Generic skill outcome ({skill})",
        description=(
            f"A trace-derived baseline verifier for the {skill} skill: did the "
            "invocation read context, produce concrete changes, run verification, and "
            "land an honest outcome? Proposed from universal trace signal families; "
            "intended as a human-reviewed DRAFT, not a curated semantic verifier."
        ),
        semantic_target=(
            "did the skill invocation read context, produce verified changes, and land "
            "an honest (non-abandoned) outcome?"
        ),
        contract_elements=_generic_contract_elements(skill),
        creator_questions=(
            CreatorQuestion(
                "outcome_floor",
                "What is the minimum for a successful invocation of this skill?",
                "Read context + produced changes that ran verification and landed.",
                "Universal trace signal families; refine per skill after human review.",
                options=("changes_landed", "verified_and_landed", "context_only"),
            ),
            CreatorQuestion(
                "verification_required",
                "Is running a verification command required for full reward?",
                "Yes — verification_run is high-weight; partial credit without it.",
                "br/51: executable verification is the strongest outcome signal.",
                options=("required", "bonus"),
            ),
            CreatorQuestion(
                "promote_to_curated",
                "Should this draft be hand-specialized into a curated archetype?",
                "Pending human review — generic drafts are advisory until approved.",
                "br/56: generic, text-adjacent markers are the highest reward-hacking risk.",
                options=("specialize", "keep_generic", "discard"),
            ),
        ),
        live_legs=(
            LiveLeg(
                "generic_agent_rerollout",
                "agent_rerollout",
                "Re-roll the agent on the task and score whether it read context, ran "
                "verification, and landed. Opt-in, real agent.",
                requires=("real_agent", "OT_REAL_REPL"),
            ),
        ),
        baseline_skill=f"# {skill} (baseline)\n\nFollow the {skill} skill.\n",
    )


def get_archetype(archetype_id: str) -> VerifierArchetype:
    try:
        return ARCHETYPES[archetype_id]
    except KeyError as exc:  # pragma: no cover - guard
        raise KeyError(f"unknown verifier archetype: {archetype_id!r}") from exc


def resolve_archetype(skill: str, archetype_id: str) -> VerifierArchetype:
    """Resolve a (skill, archetype_id) pair to an archetype.

    The skill-agnostic ``generic_skill_outcome_v1`` is built bound to ``skill``;
    curated archetype ids must target ``skill``.
    """
    if archetype_id == GENERIC_ARCHETYPE_ID:
        return generic_archetype_for(skill)
    archetype = get_archetype(archetype_id)
    if archetype.skill != skill:
        raise ValueError(
            f"archetype {archetype_id!r} targets skill {archetype.skill!r}, not {skill!r}"
        )
    return archetype


def archetypes_for_skill(skill: str) -> tuple[VerifierArchetype, ...]:
    """Curated archetypes for ``skill`` if any, else the generic trace-derived one."""
    curated = ARCHETYPES_BY_SKILL.get(skill, ())
    return curated or (generic_archetype_for(skill),)


# ---------------------------------------------------------------------------
# Serialization (archetype <-> dict, for the verifier-creator authoring path)
# ---------------------------------------------------------------------------

#: Top-level archetype keys an authored spec may contain. Anything else is rejected:
#: this is the trust boundary in practice — a spec cannot carry reward/gate/split.
ARCHETYPE_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "archetype_id",
        "skill",
        "title",
        "description",
        "semantic_target",
        "contract_elements",
        "creator_questions",
        "live_legs",
        "baseline_skill",
        "min_traces_for_split",
    }
)
_ELEMENT_KEYS: frozenset[str] = frozenset({"element_id", "label", "marker", "detectors", "weight"})
_QUESTION_KEYS: frozenset[str] = frozenset({"key", "question", "inferred_default", "rationale", "options"})
_LEG_KEYS: frozenset[str] = frozenset({"leg_id", "kind", "description", "requires"})

#: Frozen authored-archetype schema version (bump on shape change).
ARCHETYPE_SCHEMA_VERSION = "opentraces.skill_verifier_archetype.v1"


def archetype_to_dict(archetype: VerifierArchetype) -> dict[str, Any]:
    """Serialize an archetype (with declarative detectors) to a plain dict."""
    return {
        "schema_version": ARCHETYPE_SCHEMA_VERSION,
        "archetype_id": archetype.archetype_id,
        "skill": archetype.skill,
        "title": archetype.title,
        "description": archetype.description,
        "semantic_target": archetype.semantic_target,
        "min_traces_for_split": archetype.min_traces_for_split,
        "baseline_skill": archetype.baseline_skill,
        "contract_elements": [el.to_dict() for el in archetype.contract_elements],
        "creator_questions": [q.to_dict() for q in archetype.creator_questions],
        "live_legs": [leg.to_dict() for leg in archetype.live_legs],
    }


def validate_archetype_dict(payload: Any) -> None:
    """Structurally validate an authored archetype dict; reject unknown/unsafe keys."""
    if not isinstance(payload, dict):
        raise ArchetypeSpecError(f"archetype: expected an object, got {type(payload).__name__}")
    unknown = [k for k in payload if k not in ARCHETYPE_KEYS]
    if unknown:
        raise ArchetypeSpecError(
            f"archetype: unknown keys {sorted(unknown)} (a spec cannot set reward/gate/split)"
        )
    for key in ("archetype_id", "skill", "title", "semantic_target"):
        if not isinstance(payload.get(key), str) or not payload.get(key):
            raise ArchetypeSpecError(f"archetype: {key} is required and must be a non-empty string")
    elements = payload.get("contract_elements")
    if not isinstance(elements, list) or not elements:
        raise ArchetypeSpecError("archetype: contract_elements must be a non-empty list")
    markers: set[str] = set()
    for el in elements:
        if not isinstance(el, dict):
            raise ArchetypeSpecError("archetype: each contract element must be an object")
        bad = [k for k in el if k not in _ELEMENT_KEYS]
        if bad:
            raise ArchetypeSpecError(f"archetype element: unknown keys {sorted(bad)}")
        for key in ("element_id", "label", "marker"):
            if not isinstance(el.get(key), str) or not el.get(key):
                raise ArchetypeSpecError(f"archetype element: {key} is required")
        if el["marker"] in markers:
            raise ArchetypeSpecError(f"archetype element: duplicate marker {el['marker']!r}")
        markers.add(el["marker"])
        weight = el.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
            raise ArchetypeSpecError(f"archetype element {el['element_id']!r}: weight must be a positive number")
        try:
            detector_from_dict(el.get("detectors", {}), what=f"element {el.get('element_id')!r} detectors")
        except DetectorSpecError as exc:
            raise ArchetypeSpecError(str(exc)) from exc
    for q in payload.get("creator_questions", []) or []:
        if not isinstance(q, dict) or [k for k in q if k not in _QUESTION_KEYS]:
            raise ArchetypeSpecError("archetype: malformed creator_question")
    for leg in payload.get("live_legs", []) or []:
        if not isinstance(leg, dict) or [k for k in leg if k not in _LEG_KEYS]:
            raise ArchetypeSpecError("archetype: malformed live_leg")
        if leg.get("kind") not in {"agent_rerollout", "llm_rubric"}:
            raise ArchetypeSpecError(f"archetype live_leg: kind must be agent_rerollout|llm_rubric, got {leg.get('kind')!r}")


def archetype_from_dict(payload: Any) -> VerifierArchetype:
    """Validate + build a :class:`VerifierArchetype` from a dict (round-trips ``to_dict``).

    No ``eval``, no callable: detectors are interpreted from data. Unknown keys are
    rejected by :func:`validate_archetype_dict`, so a spec can never alter reward/gate.
    """
    validate_archetype_dict(payload)
    elements = tuple(
        ContractElement(
            element_id=el["element_id"],
            label=el["label"],
            marker=el["marker"],
            detectors=detector_from_dict(el.get("detectors", {}), what=f"element {el['element_id']!r} detectors"),
            weight=float(el.get("weight", 1.0)),
        )
        for el in payload["contract_elements"]
    )
    questions = tuple(
        CreatorQuestion(
            key=q["key"],
            question=q.get("question", ""),
            inferred_default=q.get("inferred_default", ""),
            rationale=q.get("rationale", ""),
            options=tuple(q.get("options", ()) or ()),
        )
        for q in payload.get("creator_questions", []) or []
    )
    legs = tuple(
        LiveLeg(
            leg_id=leg["leg_id"],
            kind=leg["kind"],
            description=leg.get("description", ""),
            requires=tuple(leg.get("requires", ()) or ()),
        )
        for leg in payload.get("live_legs", []) or []
    )
    return VerifierArchetype(
        archetype_id=payload["archetype_id"],
        skill=payload["skill"],
        title=payload["title"],
        description=payload.get("description", ""),
        semantic_target=payload["semantic_target"],
        contract_elements=elements,
        creator_questions=questions,
        live_legs=legs,
        baseline_skill=payload.get("baseline_skill") or f"# {payload['skill']} (baseline)\n",
        min_traces_for_split=int(payload.get("min_traces_for_split", 6)),
    )


def archetype_permissiveness_flags(archetype: VerifierArchetype) -> list[str]:
    """Aggregate per-element detector over-permissiveness flags (advisory)."""
    from .detectors import detector_permissiveness_flags

    flags: list[str] = []
    for el in archetype.contract_elements:
        flags.extend(detector_permissiveness_flags(el.detectors, el.element_id))
    return flags


#: The bucket-derived examples the factory ships by default: four curated archetypes
#: plus one generic, trace-derived draft over a skill with no curated archetype
#: (architecture-patterns) to demonstrate genericity end-to-end.
DEFAULT_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("goal-forge", "goal_contract_downstream_outcome_v1"),
    ("tdd", "tdd_red_green_refactor_v1"),
    ("review", "review_grounded_findings_v1"),
    ("docs-update", "docs_update_reflects_change_v1"),
    ("architecture-patterns", GENERIC_ARCHETYPE_ID),
)
