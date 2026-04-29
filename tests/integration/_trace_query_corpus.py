"""Synthetic corpus generator for the Plan-59 parity harness.

Goal: emit a representative population of traces into both the canonical
``projects/<slug>/traces/`` layer and the staging ``staging/`` layer so that
every filter in the matrix has at least one true positive *and* at least
one true negative. Covers each bug class:

- Bug #1 staging coverage: traces only present in staging.
- Bug #2 child-unit rollup: traces whose facet evidence requires
  skill_invocation / tool_sequence / patch units.
- Bug #3 supersession: pairs sharing ``session_id`` with and without an
  explicit ``metadata.superseded_by``.
- Bug #4 outcome tri-state: traces with ``outcome.success`` True, False,
  and missing.
- Bug #5 git_link_tier multi-valued: traces with ≥2 ``git_links``.
- Bug #6 survival derivation: traces whose ``git_links[].content_alive``
  liveness flags drive the survival vocabulary.
- Bug #7-#9 closed vocabularies: traces with edits + reads + bash tests
  + service probes.
- Bug #10 URL pollution: traces whose bash commands contain
  ``https://$DOMAIN/...`` shell-var URLs alongside real URLs.
- Bug #11 cmd-family / dependency garbage: traces with ``for`` loops,
  ``# comments``, and dependency lists containing junk tokens.
- Bug #12 boolean facet case-fold: schema booleans for ``outcome.success``.
"""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.core import paths


CANONICAL_PROJECT_SLUG = "parity-canonical-9f2c1e"
CANONICAL_PROJECT_ID = "9f2c1e1234567890abcdef1234567890"


def write_corpus(*, scale: int = 1) -> dict[str, list[dict]]:
    """Write the synthetic corpus and return ``{layer: [trace_dict,...]}``.

    The base population (24 traces) covers every bug class. ``scale`` can
    be used to multiply the population for speed-budget validation — each
    repeat appends ``-N`` to the original trace_id so the resulting trace
    ids stay unique. ``write_corpus(scale=20)`` produces ~480 traces,
    above the plan's 500-trace CI floor.

    Idempotent — old shards are unlinked before the new ones are written
    so a test reusing a tmp_path can re-seed without leftovers.
    """

    canonical_root = paths.PROJECTS_DIR / CANONICAL_PROJECT_SLUG
    traces_dir = canonical_root / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    (canonical_root / ".opentraces.json").write_text(
        json.dumps(
            {
                "marker_version": "2",
                "project_id": CANONICAL_PROJECT_ID,
            }
        )
    )

    staging_root = paths.STAGING_DIR
    staging_root.mkdir(parents=True, exist_ok=True)

    for path in traces_dir.glob("*.jsonl"):
        path.unlink()
    for path in staging_root.glob("*.jsonl"):
        path.unlink()

    canonical: list[dict] = []
    staging: list[dict] = []

    base_canonical = _canonical_traces()
    base_staging = _staging_traces()

    for repeat in range(scale):
        for trace in base_canonical:
            replica = _replicate(trace, repeat)
            canonical.append(replica)
            (traces_dir / f"{replica['trace_id']}.jsonl").write_text(
                json.dumps(replica) + "\n"
            )
        for trace in base_staging:
            replica = _replicate(trace, repeat)
            staging.append(replica)
            (staging_root / f"{replica['trace_id']}.jsonl").write_text(
                json.dumps(replica) + "\n"
            )
    return {"canonical": canonical, "staging": staging}


def _replicate(trace: dict, repeat: int) -> dict:
    """Emit a deep copy with id/session disambiguation when ``repeat>0``."""

    if repeat == 0:
        return json.loads(json.dumps(trace))
    suffix = f"-r{repeat:02d}"
    cloned = json.loads(json.dumps(trace))
    cloned["trace_id"] = cloned["trace_id"] + suffix
    if cloned.get("session_id"):
        cloned["session_id"] = cloned["session_id"] + suffix
    md = cloned.get("metadata") or {}
    if "superseded_by" in md:
        md["superseded_by"] = md["superseded_by"] + suffix
        cloned["metadata"] = md
    # Renumber tool_call_ids to keep them unique across replicas.
    for step in cloned.get("steps", []):
        for call in step.get("tool_calls", []):
            if "tool_call_id" in call:
                call["tool_call_id"] = call["tool_call_id"] + suffix
        for obs in step.get("observations", []):
            if "source_call_id" in obs:
                obs["source_call_id"] = obs["source_call_id"] + suffix
    return cloned


def _trace(
    trace_id: str,
    *,
    session_id: str | None = None,
    skill: str | None = None,
    tool_calls: list[dict] | None = None,
    files: list[str] | None = None,
    file_op: str | None = None,
    bash_commands: list[str] | None = None,
    outcome_success: bool | None = None,
    outcome_committed: bool = False,
    git_links: list[dict] | None = None,
    dependencies: list[str] | None = None,
    timestamp: str = "2026-04-15T12:00:00Z",
    agent_name: str = "claude-code",
    agent_model: str = "anthropic/claude-opus-4-6",
    metadata: dict | None = None,
    description: str = "Synthetic parity-harness trace",
) -> dict:
    steps: list[dict] = [
        {
            "step_index": 1,
            "role": "user",
            "content": description,
            "tool_calls": [],
            "observations": [],
        }
    ]
    step_index = 2
    if skill:
        steps.append(
            {
                "step_index": step_index,
                "role": "agent",
                "content": "",
                "tool_calls": [
                    {
                        "tool_call_id": f"{trace_id}-skill",
                        "tool_name": "Skill",
                        "input": {"name": skill},
                    }
                ],
                "observations": [
                    {
                        "source_call_id": f"{trace_id}-skill",
                        "content": "skill loaded",
                        "output_summary": "ok",
                    }
                ],
            }
        )
        step_index += 1
    if tool_calls:
        steps.append(
            {
                "step_index": step_index,
                "role": "agent",
                "content": "",
                "tool_calls": tool_calls,
                "observations": [
                    {
                        "source_call_id": tc["tool_call_id"],
                        "content": "ok",
                        "output_summary": "ok",
                    }
                    for tc in tool_calls
                ],
            }
        )
        step_index += 1
    if file_op:
        target_file = (files or ["src/edit.py"])[0]
        if file_op == "edit":
            tool_name = "Edit"
            tool_input: dict = {
                "file_path": target_file,
                "old_string": "old",
                "new_string": "new",
            }
        else:
            tool_name = "Read"
            # Trace Map's read-side file extractor reads keys
            # ("file", "path"), not "file_path". Match that convention so
            # the synthetic Read tool calls actually flow into
            # ``files_read``.
            tool_input = {"path": target_file}
        steps.append(
            {
                "step_index": step_index,
                "role": "agent",
                "content": "",
                "tool_calls": [
                    {
                        "tool_call_id": f"{trace_id}-fileop",
                        "tool_name": tool_name,
                        "input": tool_input,
                    }
                ],
                "observations": [
                    {
                        "source_call_id": f"{trace_id}-fileop",
                        "content": "ok",
                        "output_summary": "ok",
                    }
                ],
            }
        )
        step_index += 1
    if bash_commands:
        steps.append(
            {
                "step_index": step_index,
                "role": "agent",
                "content": "",
                "tool_calls": [
                    {
                        "tool_call_id": f"{trace_id}-bash{idx}",
                        "tool_name": "Bash",
                        "input": {"command": cmd},
                    }
                    for idx, cmd in enumerate(bash_commands)
                ],
                "observations": [
                    {
                        "source_call_id": f"{trace_id}-bash{idx}",
                        "content": "ok",
                        "output_summary": "ok",
                    }
                    for idx, _ in enumerate(bash_commands)
                ],
            }
        )
        step_index += 1

    outcome: dict = {
        "signal_source": "deterministic",
        "signal_confidence": "derived",
        "committed": outcome_committed,
    }
    if outcome_success is not None:
        outcome["success"] = outcome_success
    obj = {
        "schema_version": "0.4.0",
        "trace_id": trace_id,
        "session_id": session_id or trace_id,
        "timestamp_start": timestamp,
        "timestamp_end": timestamp,
        "task": {"description": description, "source": "user_prompt"},
        "agent": {"name": agent_name, "model": agent_model},
        "environment": {"os": "darwin"},
        "steps": steps,
        "outcome": outcome,
        "git_links": git_links or [],
        "dependencies": dependencies or [],
        "metadata": metadata or {},
        "metrics": {},
    }
    return obj


def _canonical_traces() -> list[dict]:
    traces: list[dict] = []

    # 1. Skill agent-browser, edit, success, committed.
    traces.append(
        _trace(
            "p59-c-001",
            skill="agent-browser",
            file_op="edit",
            files=["src/site/page.tsx"],
            outcome_success=True,
            outcome_committed=True,
            git_links=[{"vcs_type": "git", "revision": "abc1", "tier": "tool_emitted", "content_alive": True}],
            dependencies=["next", "react"],
            description="Use agent-browser to fix a UI regression",
        )
    )
    # 2. Same skill agent-browser, success, uncommitted, staging-only mirror later.
    traces.append(
        _trace(
            "p59-c-002",
            skill="agent-browser",
            file_op="edit",
            files=["src/site/api.tsx"],
            outcome_success=True,
            outcome_committed=False,
            git_links=[{"vcs_type": "git", "revision": "abc2", "tier": "overlapping"}],
            description="agent-browser on uncommitted changes",
        )
    )
    # 3. Skill grill-me, bash test, success.
    traces.append(
        _trace(
            "p59-c-003",
            skill="grill-me",
            file_op="edit",
            files=["src/parser.py"],
            bash_commands=["pytest tests/test_parser.py -v"],
            outcome_success=True,
            outcome_committed=True,
            git_links=[{"vcs_type": "git", "revision": "abc3", "tier": "tool_emitted", "content_alive": True}],
            dependencies=["pytest", "pydantic"],
            description="grill-me to validate parser logic",
        )
    )
    # 4. Skill deep-research, no edit, success unknown (None).
    traces.append(
        _trace(
            "p59-c-004",
            skill="deep-research",
            tool_calls=[
                {
                    "tool_call_id": "p59-c-004-grep",
                    "tool_name": "Grep",
                    "input": {"pattern": "research", "path": "kb/"},
                }
            ],
            files=["kb/notes.md"],
            outcome_success=None,
            description="deep-research synthesis",
        )
    )
    # 5. Bash service probe (curl), success False.
    traces.append(
        _trace(
            "p59-c-005",
            bash_commands=["curl https://api.example.com/health"],
            outcome_success=False,
            outcome_committed=False,
            description="probe healthcheck",
        )
    )
    # 6. Bash with shell-var URL pollution (must NOT emit service.name).
    traces.append(
        _trace(
            "p59-c-006",
            bash_commands=[
                "curl https://$DOMAIN/api/health",
                "for f in *.py; do echo $f; done",
                "# inline comment line",
            ],
            outcome_success=True,
            outcome_committed=False,
            description="shell-var URL plus garbage cmd-family inputs",
        )
    )
    # 7. Dependencies with junk tokens — only valid ones must surface.
    traces.append(
        _trace(
            "p59-c-007",
            dependencies=[
                "requests",
                "2",
                "#",
                "for",
                "flask",
                "ham\\eggs",
            ],
            outcome_success=True,
            outcome_committed=True,
            description="dependency hygiene smoke",
        )
    )
    # 8. Multi-tier git_links (overlapping + tool_emitted).
    traces.append(
        _trace(
            "p59-c-008",
            file_op="edit",
            files=["src/multi.py"],
            git_links=[
                {"vcs_type": "git", "revision": "m1", "tier": "tool_emitted", "content_alive": True},
                {"vcs_type": "git", "revision": "m2", "tier": "overlapping", "content_alive": True},
                {"vcs_type": "git", "revision": "m3", "tier": "tool_emitted_with_divergence", "content_alive": True},
            ],
            outcome_success=True,
            outcome_committed=True,
            description="trace whose tiers must each be filterable",
        )
    )
    # 9. Survival lost via content_alive=False, commit_reachable=True.
    traces.append(
        _trace(
            "p59-c-009",
            file_op="edit",
            files=["src/lost.py"],
            git_links=[{"vcs_type": "git", "revision": "lo1", "tier": "tool_emitted", "content_alive": False, "commit_reachable": True}],
            outcome_success=True,
            outcome_committed=True,
            description="lost survival smoke",
        )
    )
    # 10. Survival reverted via commit_reachable=False.
    traces.append(
        _trace(
            "p59-c-010",
            file_op="edit",
            files=["src/reverted.py"],
            git_links=[{"vcs_type": "git", "revision": "rv1", "tier": "overlapping", "content_alive": False, "commit_reachable": False}],
            outcome_success=False,
            outcome_committed=False,
            description="reverted survival smoke",
        )
    )
    # 11. Survival declared explicitly via metadata.survival_state.
    traces.append(
        _trace(
            "p59-c-011",
            file_op="edit",
            files=["src/declared.py"],
            git_links=[{"vcs_type": "git", "revision": "dc1", "tier": "tool_emitted"}],
            metadata={"survival_state": "alive_transformed"},
            outcome_success=True,
            outcome_committed=True,
            description="declared alive_transformed",
        )
    )
    # 12. Codex provider via gpt-5 model.
    traces.append(
        _trace(
            "p59-c-012",
            agent_name="codex",
            agent_model="openai/gpt-5",
            file_op="edit",
            files=["src/codex.py"],
            outcome_success=True,
            outcome_committed=True,
            description="codex provider trace",
        )
    )
    # 13. Read-only file op.
    traces.append(
        _trace(
            "p59-c-013",
            tool_calls=[
                {"tool_call_id": "p59-c-013-r", "tool_name": "Read", "input": {"path": "src/just_read.py"}}
            ],
            outcome_success=True,
            outcome_committed=False,
            description="read-only operation",
        )
    )
    # 14-15. Supersession pair (one explicitly superseded).
    traces.append(
        _trace(
            "p59-c-014-old",
            session_id="parity-supersess-1",
            skill="agent-browser",
            file_op="edit",
            files=["src/site/replaced.tsx"],
            outcome_success=True,
            outcome_committed=False,
            metadata={"superseded_by": "p59-c-015-new"},
            description="superseded sibling — must be hidden under default supersession",
        )
    )
    traces.append(
        _trace(
            "p59-c-015-new",
            session_id="parity-supersess-1",
            skill="agent-browser",
            file_op="edit",
            files=["src/site/final.tsx"],
            outcome_success=True,
            outcome_committed=True,
            description="surviving sibling",
        )
    )
    # 16. Same session_id, different content, NOT marked superseded.
    traces.append(
        _trace(
            "p59-c-016-a",
            session_id="parity-distinct-1",
            skill="grill-me",
            file_op="edit",
            files=["src/distinct_a.py"],
            outcome_success=True,
            description="distinct sibling A",
        )
    )
    traces.append(
        _trace(
            "p59-c-017-b",
            session_id="parity-distinct-1",
            skill="grill-me",
            file_op="edit",
            files=["src/distinct_b.py"],
            outcome_success=True,
            description="distinct sibling B",
        )
    )
    # 18. Old timestamp for --since smoke.
    traces.append(
        _trace(
            "p59-c-018-old",
            timestamp="2025-12-15T12:00:00Z",
            file_op="read",
            outcome_success=True,
            description="historical trace",
        )
    )
    # 19. Recent timestamp for --since smoke.
    traces.append(
        _trace(
            "p59-c-019-recent",
            timestamp="2026-04-29T12:00:00Z",
            skill="agent-browser",
            file_op="edit",
            outcome_success=True,
            description="recent trace",
        )
    )
    # 20. Trace mentioning a real URL (service probe).
    traces.append(
        _trace(
            "p59-c-020",
            bash_commands=["curl https://hub.huggingface.co/api/datasets"],
            outcome_success=True,
            description="hf service probe",
        )
    )
    # 21. Tested-successful-fix-candidate (bug_fix) — needs failed-then-passed
    # observations and a file_edit. The trace_signal derivation lights up
    # ``tested_successful_fix_candidate`` only when all preconditions land.
    fix = _trace(
        "p59-c-021-bugfix",
        skill="grill-me",
        file_op="edit",
        files=["src/tested.py"],
        outcome_success=True,
        outcome_committed=True,
        git_links=[{"vcs_type": "git", "revision": "fx1", "tier": "tool_emitted"}],
        dependencies=["pytest"],
        description="bug fix candidate trace mentioning parser regression",
    )
    fix["steps"].append(
        {
            "step_index": 99,
            "role": "agent",
            "content": "",
            "tool_calls": [
                {
                    "tool_call_id": "p59-c-021-fail",
                    "tool_name": "Bash",
                    "input": {"command": "pytest tests/test_tested.py"},
                },
                {
                    "tool_call_id": "p59-c-021-pass",
                    "tool_name": "Bash",
                    "input": {"command": "pytest tests/test_tested.py"},
                },
            ],
            "observations": [
                {
                    "source_call_id": "p59-c-021-fail",
                    "content": "FAILED tests/test_tested.py::test_parser_regression",
                    "output_summary": "1 failed",
                },
                {
                    "source_call_id": "p59-c-021-pass",
                    "content": "tests/test_tested.py . 1 passed",
                    "output_summary": "1 passed",
                },
            ],
        }
    )
    traces.append(fix)
    return traces


def _staging_traces() -> list[dict]:
    """Traces only present in the staging layer.

    These exist to drive Bug #1 (staging coverage) — every filter that
    matches them via the canonical layer must also match them via staging.
    """

    return [
        _trace(
            "p59-s-001",
            skill="agent-browser",
            file_op="edit",
            files=["src/staging/staged_a.tsx"],
            outcome_success=True,
            outcome_committed=False,
            description="staged trace — must be discoverable via --skill agent-browser",
        ),
        _trace(
            "p59-s-002",
            skill="grill-me",
            file_op="edit",
            files=["src/staging/staged_b.py"],
            bash_commands=["pytest tests/staged.py"],
            outcome_success=True,
            description="staged grill-me trace",
        ),
        _trace(
            "p59-s-003",
            agent_name="codex",
            agent_model="openai/gpt-5",
            file_op="read",
            outcome_success=False,
            description="staged codex read",
        ),
        _trace(
            "p59-s-004",
            git_links=[{"vcs_type": "git", "revision": "stg-1", "tier": "overlapping"}],
            outcome_success=True,
            description="staged git_link_tier=overlapping",
        ),
    ]


def fixture_project_slug() -> str:
    return CANONICAL_PROJECT_SLUG


__all__ = ["write_corpus", "fixture_project_slug", "CANONICAL_PROJECT_SLUG"]
