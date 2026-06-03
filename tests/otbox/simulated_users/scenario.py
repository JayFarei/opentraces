"""Scenario TOML schema + parser + validator (plan 071, R2).

A *scenario* is a TOML document under ``scenarios/<name>.toml`` that
declares one interactive prompt sequence to drive against an agent
binary (real ``claude`` / ``codex`` / ``hermes``, or the synthetic
``echo`` meta-test binary). The parser produces a :class:`Scenario`
dataclass that the PTY runner (Agent A, ``runner.py``) and the
``capture-refresh`` CLI (Agent C) consume.

Scenarios are intentionally declarative — adding coverage means
dropping a new ``.toml`` file under ``scenarios/`` and (optionally)
adding a fresh seed template under ``templates/``, not editing this
module.

Validation rules (per plan 071 R2):
  * required top-level keys: ``name``, ``agent``, ``binary_name``,
    ``[capture]`` table, at least one ``[[turns]]`` entry.
  * ``agent`` must be one of ``SUPPORTED_AGENTS``.
  * every ``[[turns]]`` entry must define ``prompt`` AND
    ``expect_regex``; ``timeout_s`` defaults to ``60.0``.
  * ``[capture]`` must define ``artifact_dir``; ``expected_paths`` is
    optional and defaults to an empty list.
  * when ``[initial_state]`` declares ``template``, the template name
    must resolve to a real directory under :data:`TEMPLATES_DIR`.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Turn dataclass — owned by Agent A's runner.py, re-exported here so that
# scenario consumers (Agent C, tests) can pull it from one place. If
# runner.py exists at import time, we use its definition; otherwise we
# fall back to a structurally identical local copy that will merge
# transparently when Agent A's module lands (dataclass-equality is by
# field name + type).
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised once Agent A lands runner.py
    from .runner import Turn  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001 - runner not yet available; use local shim

    @dataclass
    class Turn:
        """One prompt → expect_regex interaction with the agent binary.

        Local shim used until Agent A's ``runner.py`` lands. The field
        names + types match Agent A's contract verbatim so swapping
        modules is a no-op for any downstream consumer.
        """

        prompt: str
        expect_regex: str
        timeout_s: float = 60.0


__all__ = [
    "InitialState",
    "CaptureSpec",
    "Scenario",
    "ScenarioError",
    "Turn",
    "SCENARIOS_DIR",
    "TEMPLATES_DIR",
    "SUPPORTED_AGENTS",
    "available_scenarios",
    "load_scenario",
    "scenario_digest",
]


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
SCENARIOS_DIR: Path = Path(__file__).resolve().parent / "scenarios"
TEMPLATES_DIR: Path = Path(__file__).resolve().parent / "templates"

SUPPORTED_AGENTS: tuple[str, ...] = ("claude", "codex", "pi", "hermes", "echo")


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------
@dataclass
class InitialState:
    """Seed state copied into the box's project dir before the run.

    ``template_name`` is the raw name read from the TOML (or ``None``
    when ``[initial_state]`` is absent / empty). ``template_dir`` is
    the resolved absolute path under :data:`TEMPLATES_DIR`, populated
    by :func:`load_scenario` after validation.
    """

    template_name: str | None = None
    template_dir: Path | None = None


@dataclass
class CaptureSpec:
    """Where the runner stages outputs + what files must end up present.

    ``artifact_dir_name`` is the leaf directory name under
    ``tests/otbox/captures/<name>/`` (e.g. ``"echo-meta"``). The
    surrounding ``captures/`` parent is owned by Agent C's CLI surface
    — this dataclass holds only the per-scenario leaf.

    ``expected_paths`` is the list of paths (relative to the box
    project) that MUST exist after the scenario completes. The runner
    treats a missing path as a hard failure.
    """

    artifact_dir_name: str
    expected_paths: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    """A parsed, validated scenario ready for execution.

    Produced by :func:`load_scenario`. ``source_path`` records the TOML
    file the scenario was parsed from so error messages can point at
    the right line, and ``scenario_digest(scenario)`` can stamp
    provenance into capture metadata.
    """

    name: str
    description: str
    agent: str
    binary_name: str
    initial_state: InitialState
    turns: list[Turn]
    capture: CaptureSpec
    source_path: Path


class ScenarioError(Exception):
    """Raised on any scenario-load or scenario-validation failure."""


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------
def _scenario_path(name: str) -> Path:
    """Resolve ``name`` to a TOML path under :data:`SCENARIOS_DIR`."""
    path = SCENARIOS_DIR / f"{name}.toml"
    if not path.exists():
        available = [p.stem for p in sorted(SCENARIOS_DIR.glob("*.toml"))]
        raise ScenarioError(
            f"no scenario {name!r} in catalogue ({SCENARIOS_DIR}); "
            f"available: {available}"
        )
    return path


def available_scenarios() -> list[dict]:
    """Discover scenarios by glob and return summary dicts.

    Mirrors :func:`tests.otbox.journey.available_journeys` — keeps the
    listing surface uniform between journeys and scenarios so the same
    CLI patterns work across both.

    Malformed TOML files are reported as one entry with ``error`` set
    so the catalogue listing never crashes on a single bad scenario.
    """
    out: list[dict] = []
    if not SCENARIOS_DIR.exists():
        return out
    for path in sorted(SCENARIOS_DIR.glob("*.toml")):
        try:
            doc = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            out.append(
                {
                    "name": path.stem,
                    "description": "",
                    "agent": "",
                    "binary_name": "",
                    "turn_count": 0,
                    "error": f"TOML parse error: {exc}",
                }
            )
            continue
        turns = doc.get("turns") or []
        out.append(
            {
                "name": doc.get("name", path.stem),
                "description": str(doc.get("description", "")).strip(),
                "agent": doc.get("agent", ""),
                "binary_name": doc.get("binary_name", ""),
                "turn_count": len(turns),
            }
        )
    return out


# ---------------------------------------------------------------------------
# load + validate
# ---------------------------------------------------------------------------
def _require(doc: dict, key: str, where: str) -> Any:
    if key not in doc:
        raise ScenarioError(f"{where}: missing required key {key!r}")
    return doc[key]


def _coerce_turns(raw_turns: Any, where: str) -> list[Turn]:
    if not isinstance(raw_turns, list):
        raise ScenarioError(f"{where}: 'turns' must be an array of tables")
    if not raw_turns:
        raise ScenarioError(f"{where}: 'turns' must contain at least one entry")
    out: list[Turn] = []
    for idx, raw in enumerate(raw_turns):
        if not isinstance(raw, dict):
            raise ScenarioError(
                f"{where}: turns[{idx}] must be a table, got {type(raw).__name__}"
            )
        if "prompt" not in raw or not str(raw["prompt"]).strip():
            raise ScenarioError(
                f"{where}: turns[{idx}] is missing required 'prompt'"
            )
        if "expect_regex" not in raw or not str(raw["expect_regex"]).strip():
            raise ScenarioError(
                f"{where}: turns[{idx}] is missing required 'expect_regex'"
            )
        timeout_raw = raw.get("timeout_s", 60.0)
        try:
            timeout_s = float(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise ScenarioError(
                f"{where}: turns[{idx}].timeout_s must be a number, "
                f"got {timeout_raw!r}"
            ) from exc
        if timeout_s <= 0:
            raise ScenarioError(
                f"{where}: turns[{idx}].timeout_s must be > 0 "
                f"(got {timeout_s})"
            )
        out.append(
            Turn(
                prompt=str(raw["prompt"]),
                expect_regex=str(raw["expect_regex"]),
                timeout_s=timeout_s,
            )
        )
    return out


def _coerce_capture(raw_capture: Any, where: str) -> CaptureSpec:
    if not isinstance(raw_capture, dict):
        raise ScenarioError(
            f"{where}: [capture] must be a table, got {type(raw_capture).__name__}"
        )
    artifact_dir = raw_capture.get("artifact_dir")
    if not artifact_dir or not str(artifact_dir).strip():
        raise ScenarioError(
            f"{where}: [capture] is missing required 'artifact_dir'"
        )
    artifact_dir = str(artifact_dir).strip()
    # Reject absolute / parent-traversing paths — capture artifacts live
    # under tests/otbox/captures/<name>/, the leaf is just a name.
    if (
        artifact_dir.startswith("/")
        or ".." in Path(artifact_dir).parts
        or Path(artifact_dir).is_absolute()
    ):
        raise ScenarioError(
            f"{where}: [capture].artifact_dir must be a relative leaf name, "
            f"got {artifact_dir!r}"
        )

    expected_paths_raw = raw_capture.get("expected_paths", [])
    if not isinstance(expected_paths_raw, list):
        raise ScenarioError(
            f"{where}: [capture].expected_paths must be an array of strings"
        )
    expected_paths = [str(p) for p in expected_paths_raw]
    return CaptureSpec(
        artifact_dir_name=artifact_dir,
        expected_paths=expected_paths,
    )


def _coerce_initial_state(raw_state: Any, where: str) -> InitialState:
    if raw_state is None:
        return InitialState()
    if not isinstance(raw_state, dict):
        raise ScenarioError(
            f"{where}: [initial_state] must be a table, "
            f"got {type(raw_state).__name__}"
        )
    template_raw = raw_state.get("template")
    if template_raw is None or not str(template_raw).strip():
        return InitialState()
    template_name = str(template_raw).strip()
    template_dir = (TEMPLATES_DIR / template_name).resolve()
    # Guard against template names that escape TEMPLATES_DIR via .. .
    try:
        template_dir.relative_to(TEMPLATES_DIR.resolve())
    except ValueError as exc:
        raise ScenarioError(
            f"{where}: [initial_state].template {template_name!r} escapes "
            f"templates dir"
        ) from exc
    if not template_dir.exists() or not template_dir.is_dir():
        available = (
            sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir())
            if TEMPLATES_DIR.exists()
            else []
        )
        raise ScenarioError(
            f"{where}: [initial_state].template {template_name!r} does not "
            f"resolve to a directory under {TEMPLATES_DIR}; "
            f"available templates: {available}"
        )
    return InitialState(template_name=template_name, template_dir=template_dir)


def load_scenario(name: str) -> Scenario:
    """Load + validate the scenario named ``name``.

    Reads ``scenarios/<name>.toml``, validates the schema, resolves
    any declared template, and returns a fully populated
    :class:`Scenario`. Raises :class:`ScenarioError` on any missing
    required field, malformed turn list, unsupported agent, or
    unresolved template.
    """
    path = _scenario_path(name)
    where = f"scenario {name!r} at {path}"
    try:
        doc = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ScenarioError(f"{where}: TOML parse error: {exc}") from exc

    scenario_name = str(_require(doc, "name", where)).strip()
    if not scenario_name:
        raise ScenarioError(f"{where}: 'name' must be non-empty")

    agent = str(_require(doc, "agent", where)).strip()
    if agent not in SUPPORTED_AGENTS:
        raise ScenarioError(
            f"{where}: 'agent' must be one of {list(SUPPORTED_AGENTS)}, "
            f"got {agent!r}"
        )

    binary_name = str(_require(doc, "binary_name", where)).strip()
    if not binary_name:
        raise ScenarioError(f"{where}: 'binary_name' must be non-empty")

    description = str(doc.get("description", "")).strip()
    turns = _coerce_turns(_require(doc, "turns", where), where)
    capture = _coerce_capture(_require(doc, "capture", where), where)
    initial_state = _coerce_initial_state(doc.get("initial_state"), where)

    return Scenario(
        name=scenario_name,
        description=description,
        agent=agent,
        binary_name=binary_name,
        initial_state=initial_state,
        turns=turns,
        capture=capture,
        source_path=path,
    )


# ---------------------------------------------------------------------------
# digest — pinned in capture metadata by Agent C
# ---------------------------------------------------------------------------
def scenario_digest(scenario: Scenario) -> str:
    """SHA-256 hex digest of the scenario TOML's raw bytes.

    Stable for byte-identical inputs, changes whenever any character
    of the TOML changes (including comments / whitespace). Agent C
    stamps this into ``metadata.json`` so a capture artifact carries
    the exact scenario revision it was produced from.
    """
    return hashlib.sha256(scenario.source_path.read_bytes()).hexdigest()
