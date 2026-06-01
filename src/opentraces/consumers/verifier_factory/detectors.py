"""Declarative, serializable detector specs for verifier archetypes.

A *detector* is DATA, not code. Each :class:`DetectorSpec` is a frozen set of
predicate fields over a mined episode's normalized ``EpisodeEvidence``. The engine
(:func:`detector_matches`) interprets the spec with fixed, safe Python — there is
**no ``eval``, no arbitrary callable, and no field that can influence reward or the
optimization gate**. An element is "present" when ANY populated predicate in its
detector matches (a disjunction), which is exactly the shape every legacy closure
detector had, so the migration off closures is lossless.

The four primary detector fields named in the design are ``text_any``,
``command_families``, ``file_globs``, and ``tool_any``. Lossless migration of the
existing closures also needs a small, fixed set of additional pure-data predicates
(``command_any``, ``has_files``, ``has_commands``, ``committed``,
``outcome_reward_min``, ``outcome_label_in``); they are still data, interpreted by
the same fixed engine, never code.

Trust boundary (br/56): a detector spec describes *what to read off a trace*; it
can never set or alter the verifier reward (binary full-contract pass) or the
SkillOpt gate. Those live in :mod:`packaging` and are mechanical. The validator
hard-rejects unknown keys precisely so a hand-authored spec cannot smuggle a
``reward`` / ``gate`` / ``split`` field past the engine.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

#: Command-family classifier table (br-66/53 trace-data utilization). Codex CLI
#: wraps every action in ``exec_command`` so the tool NAME is opaque; the real
#: signal is the shell command string (``normalized_tool_calls[].shell.command``).
#: We classify those strings (and Claude tool names) into stable families so
#: detectors fire on BOTH agents instead of only on Claude's named tools. This is
#: the single source of truth shared by the classifier and the spec validator.
COMMAND_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("verification", (
        "pytest", "unittest", " test", "test ", "tests", "tox", "go test", "cargo test",
        "npm test", "npm run test", "jest", "vitest", "make test", "make check",
        "ruff", "flake8", "mypy", "tsc", "eslint", "lint", "check_app", "/check",
    )),
    ("git_commit", ("git commit", "git merge", "git rebase", "git cherry-pick")),
    ("git", ("git ",)),
    ("search_read", (
        "rg ", "grep", "cat ", "ls ", "find ", "head ", "tail ", "sed -n", "less ",
        "read", "open ", "glob",
    )),
    ("edit_write", (
        "apply_patch", "applypatch", "tee ", "sed -i", " > ", ">>", "write", "edit",
        "mkdir", "touch ", "mv ", "cp ",
    )),
    ("build", ("make ", "npm run", "npm install", "pip install", "cargo build", "go build", "yarn")),
)

#: Stable family names a detector spec may reference (validated).
KNOWN_COMMAND_FAMILIES: frozenset[str] = frozenset(name for name, _ in COMMAND_FAMILIES)


def classify_command(text: str) -> set[str]:
    """Classify a raw command / tool-name string into command families."""
    low = text.lower()
    fams: set[str] = set()
    for family, needles in COMMAND_FAMILIES:
        if any(n in low for n in needles):
            fams.add(family)
    return fams


#: The list-valued (tuple) detector fields, for validation + serialization.
_LIST_FIELDS = ("text_any", "command_families", "file_globs", "tool_any", "command_any", "outcome_label_in")
#: The bool-valued detector fields.
_BOOL_FIELDS = ("has_files", "has_commands", "committed")
#: All recognised detector keys (anything else is rejected by the validator).
DETECTOR_FIELDS: frozenset[str] = frozenset(_LIST_FIELDS + _BOOL_FIELDS + ("outcome_reward_min",))

#: Substring tokens shorter than this are flagged as over-permissive (match too much).
_MIN_TOKEN_LEN = 3


class DetectorSpecError(ValueError):
    """Raised when a detector spec dict is structurally invalid (trust boundary)."""


@dataclass(frozen=True)
class DetectorSpec:
    """A serializable, data-only predicate over ``EpisodeEvidence``.

    Element-present semantics are a disjunction: the detector matches when ANY
    populated field matches. An empty spec never matches.
    """

    text_any: tuple[str, ...] = ()
    command_families: tuple[str, ...] = ()
    file_globs: tuple[str, ...] = ()
    tool_any: tuple[str, ...] = ()
    command_any: tuple[str, ...] = ()
    has_files: bool = False
    has_commands: bool = False
    committed: bool = False
    outcome_reward_min: float | None = None
    outcome_label_in: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Emit only the populated fields, so ``spec.yaml`` reads naturally."""
        out: dict[str, Any] = {}
        for field_name in _LIST_FIELDS:
            value = getattr(self, field_name)
            if value:
                out[field_name] = list(value)
        for field_name in _BOOL_FIELDS:
            if getattr(self, field_name):
                out[field_name] = True
        if self.outcome_reward_min is not None:
            out["outcome_reward_min"] = self.outcome_reward_min
        return out

    def is_empty(self) -> bool:
        return self == DetectorSpec()

    def matches(self, evidence: Any) -> bool:
        return detector_matches(self, evidence)


def _file_match(files: tuple[str, ...], patterns: tuple[str, ...]) -> bool:
    """Match files against patterns.

    A pattern with glob metacharacters (``* ? [``) is matched with ``fnmatch``;
    a plain pattern is matched by case-insensitive substring containment. The
    substring path preserves the legacy ``EpisodeEvidence.any_file`` semantics
    exactly, so migrating those detectors is lossless.
    """
    for pattern in patterns:
        pat = pattern.lower()
        if any(ch in pat for ch in "*?["):
            if any(fnmatch.fnmatch(f.lower(), pat) for f in files):
                return True
        elif any(pat in f.lower() for f in files):
            return True
    return False


def detector_matches(spec: DetectorSpec, evidence: Any) -> bool:
    """Interpret ``spec`` against an ``EpisodeEvidence`` (fixed, safe, no eval)."""
    if spec.text_any and evidence.has_any(*spec.text_any):
        return True
    if spec.command_families and evidence.has_family(*spec.command_families):
        return True
    if spec.file_globs and _file_match(tuple(evidence.files), spec.file_globs):
        return True
    if spec.tool_any and evidence.any_tool(*spec.tool_any):
        return True
    if spec.command_any and evidence.any_command(*spec.command_any):
        return True
    if spec.has_files and bool(evidence.files):
        return True
    if spec.has_commands and bool(evidence.commands):
        return True
    if spec.committed and bool(evidence.committed):
        return True
    if spec.outcome_reward_min is not None and float(evidence.outcome_reward) >= spec.outcome_reward_min:
        return True
    if spec.outcome_label_in and evidence.outcome_label in spec.outcome_label_in:
        return True
    return False


def validate_detector_dict(payload: Any, *, what: str = "detector") -> None:
    """Structurally validate a detector dict; reject unknown / unsafe keys.

    Rejecting unknown keys is the trust boundary in practice: a hand-authored spec
    cannot introduce a ``reward`` / ``gate`` / ``split`` field — only the fixed set
    of pure-data predicates the engine knows how to read off a trace.
    """
    if not isinstance(payload, dict):
        raise DetectorSpecError(f"{what}: expected an object, got {type(payload).__name__}")
    unknown = [k for k in payload if k not in DETECTOR_FIELDS]
    if unknown:
        raise DetectorSpecError(f"{what}: unknown detector keys {sorted(unknown)} (spec cannot set reward/gate)")
    for key in _LIST_FIELDS:
        if key in payload:
            value = payload[key]
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise DetectorSpecError(f"{what}: {key} must be a list of strings")
    for key in _BOOL_FIELDS:
        if key in payload and not isinstance(payload[key], bool):
            raise DetectorSpecError(f"{what}: {key} must be a boolean")
    if "command_families" in payload:
        bad = [f for f in payload["command_families"] if f not in KNOWN_COMMAND_FAMILIES]
        if bad:
            raise DetectorSpecError(
                f"{what}: unknown command_families {bad} (known: {sorted(KNOWN_COMMAND_FAMILIES)})"
            )
    if "outcome_reward_min" in payload:
        value = payload["outcome_reward_min"]
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or not 0.0 <= float(value) <= 1.0):
            raise DetectorSpecError(f"{what}: outcome_reward_min must be a float in [0,1] or null")


def detector_from_dict(payload: Any, *, what: str = "detector") -> DetectorSpec:
    """Validate + build a :class:`DetectorSpec` from a dict (round-trips ``to_dict``)."""
    validate_detector_dict(payload, what=what)
    return DetectorSpec(
        text_any=tuple(payload.get("text_any", ())),
        command_families=tuple(payload.get("command_families", ())),
        file_globs=tuple(payload.get("file_globs", ())),
        tool_any=tuple(payload.get("tool_any", ())),
        command_any=tuple(payload.get("command_any", ())),
        has_files=bool(payload.get("has_files", False)),
        has_commands=bool(payload.get("has_commands", False)),
        committed=bool(payload.get("committed", False)),
        outcome_reward_min=(
            float(payload["outcome_reward_min"])
            if payload.get("outcome_reward_min") is not None
            else None
        ),
        outcome_label_in=tuple(payload.get("outcome_label_in", ())),
    )


def detector_permissiveness_flags(spec: DetectorSpec, element_id: str) -> list[str]:
    """Soft-lint a detector for over-permissiveness (flagged, never silently accepted).

    Two heuristics: (1) a detector whose ONLY signal is presence-of-anything
    (``has_files`` / ``has_commands`` / ``outcome_reward_min<=0``) matches almost
    every trace, so a high-weight element resting solely on it cannot discriminate;
    (2) a short substring token (``< 3`` chars) over-matches. Flags are surfaced as
    package limitations, not hard rejections — generic baselines legitimately rest on
    broad signals, which is exactly why they are never auto-``recommended``.
    """
    flags: list[str] = []
    discriminators = (
        spec.text_any or spec.command_families or spec.file_globs
        or spec.tool_any or spec.command_any or spec.outcome_label_in
        or spec.committed
    )
    broad_presence = spec.has_files or spec.has_commands or (
        spec.outcome_reward_min is not None and spec.outcome_reward_min <= 0.0
    )
    if broad_presence and not discriminators:
        flags.append(
            f"element {element_id!r}: detector relies only on presence "
            "(has_files/has_commands/outcome_reward_min<=0) — matches nearly every trace"
        )
    for field_name in ("text_any", "command_any", "file_globs", "tool_any"):
        for token in getattr(spec, field_name):
            if len(token.strip()) < _MIN_TOKEN_LEN:
                flags.append(
                    f"element {element_id!r}: {field_name} token {token!r} shorter than "
                    f"{_MIN_TOKEN_LEN} chars — over-broad substring"
                )
    return flags
